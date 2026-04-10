from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_chroma import Chroma
from langchain_community.document_loaders.base import BaseLoader
from langchain_core.documents import Document as LCDocument
from langchain_core.embeddings import Embeddings
from langchain_openai import ChatOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer

from app.config import (
    CHROMA_COLLECTION_NAME,
    CHROMA_DIR,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    EMBEDDING_MODEL_NAME,
    MINIMAX_MAX_OUTPUT_TOKENS,
    MINIMAX_MODEL_NAME,
    MINIMAX_REASONING_SPLIT,
    MINIMAX_TEMPERATURE,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
)
from app.services.minimax import MinimaxConfigError
from app.services.parsers import ParsedSection, parse_document_structure


class SentenceTransformerEmbeddings(Embeddings):
    """LangChain Embeddings 适配器。

    现有项目已经在使用 `sentence-transformers` 生成向量。
    这里不更换模型能力，只是把它包装成 LangChain 统一要求的 Embeddings 接口，
    这样后续的 Chroma 向量库、检索器都可以直接走 LangChain 标准组件。
    """

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        # 生产/内网环境下经常无法直接访问 HuggingFace。
        # 因此这里优先解析本机缓存目录，并强制 `local_files_only=True`，
        # 避免每次初始化时都去线上探测 adapter/config，导致文档上传阶段失败。
        resolved_model = resolve_sentence_transformer_path(model_name)
        self._model = SentenceTransformer(resolved_model, local_files_only=True)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        embeddings = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embeddings.tolist()

    def embed_query(self, text: str) -> list[float]:
        if not text:
            return []
        embedding = self._model.encode(
            [text],
            normalize_embeddings=True,
            show_progress_bar=False,
        )[0]
        return embedding.tolist()


class StructuredDocumentLoader(BaseLoader):
    """自定义 LangChain Loader。

    LangChain 自带了很多文件加载器，但本项目原来已经有一套较完整的文档结构解析逻辑，
    尤其是对 Word 标题层级、章节编号、正文归属的处理比较贴近当前业务。
    为了保证“业务逻辑不变”，这里采用“自定义 Loader + 复用原有解析器”的方式：

    1. 对外暴露的是 LangChain 标准 Loader，满足“文档加载使用 LangChain 框架实现”。
    2. 内部仍沿用现有结构解析规则，避免改造后切块语义发生明显漂移。
    """

    def __init__(self, file_path: str | Path) -> None:
        self.file_path = Path(file_path)

    def lazy_load(self) -> Iterator[LCDocument]:
        parsed = parse_document_structure(self.file_path)

        yielded = False
        for section in parsed.sections:
            for doc in self._yield_section_documents(section, ancestors=[]):
                yielded = True
                yield doc

        # 当结构化解析结果为空时，兜底返回整篇正文。
        if not yielded and parsed.full_text.strip():
            yield LCDocument(
                page_content=parsed.full_text.strip(),
                metadata={
                    "section_id": "fallback-body",
                    "section_title": "正文",
                    "chapter_path": "正文",
                    "section_level": 1,
                    "section_order": 1,
                    "path_depth": 1,
                    "source": str(self.file_path),
                    "loader_type": "structured_document_loader",
                },
            )

    def _yield_section_documents(
        self,
        section: ParsedSection,
        ancestors: list[str],
    ) -> Iterator[LCDocument]:
        """把章节树转换成 LangChain Document 流。

        这里故意按“章节节点”输出，而不是一开始就切到固定字数。
        原因是 LangChain 里“加载”和“切分”通常是两个阶段：
        先保留尽量完整的业务语义边界，再交给 TextSplitter 做统一切片。
        """

        path_titles = ancestors + ([section.title] if section.title else [])
        body_text = self._collect_section_body(section)
        if body_text:
            composed_content = self._compose_chunk_content(path_titles, body_text)
            yield LCDocument(
                page_content=composed_content,
                metadata={
                    "section_id": section.id,
                    "section_title": section.title or "正文",
                    "chapter_path": " / ".join(path_titles) or "正文",
                    "section_level": section.level,
                    "section_order": section.order_index,
                    "path_depth": len(path_titles) or 1,
                    "source": str(self.file_path),
                    "loader_type": "structured_document_loader",
                },
            )

        for child in section.children:
            yield from self._yield_section_documents(child, path_titles)

    @staticmethod
    def _compose_chunk_content(path_titles: list[str], body_text: str) -> str:
        prefix = " / ".join(path_titles).strip()
        if prefix:
            return f"{prefix}\n\n{body_text.strip()}".strip()
        return body_text.strip()

    @staticmethod
    def _collect_section_body(section: ParsedSection) -> str:
        texts: list[str] = []
        for block in section.blocks:
            if block["type"] == "paragraph":
                text = str(block.get("text", "")).strip()
                if text:
                    texts.append(text)
                continue
            if block["type"] == "section":
                child = block["node"]
                child_text = StructuredDocumentLoader._collect_section_body(child)
                if child_text:
                    texts.append(child_text)
        return "\n".join(texts).strip()


def resolve_sentence_transformer_path(model_name: str) -> str:
    """优先把 HuggingFace 仓库名解析到本地缓存快照目录。

    `sentence-transformers` 传仓库名时，底层通常会先访问 HuggingFace Hub 做一次元数据检查。
    当前项目部署环境未必允许外网访问，因此这里：

    1. 如果 `model_name` 本来就是本地目录，直接使用。
    2. 如果是 `owner/repo` 形式，则尝试定位 `~/.cache/huggingface/hub` 下的快照目录。
    3. 找不到时仍返回原始名称，让调用方得到明确的本地模型缺失异常。
    """

    candidate = Path(model_name)
    if candidate.exists():
        return str(candidate)

    if "/" not in model_name:
        return model_name

    owner, repo = model_name.split("/", 1)
    cache_root = Path(os.getenv("HF_HOME") or Path.home() / ".cache" / "huggingface")
    repo_root = cache_root / "hub" / f"models--{owner}--{repo}"
    snapshots_dir = repo_root / "snapshots"
    if not snapshots_dir.exists():
        return model_name

    snapshots = sorted((path for path in snapshots_dir.iterdir() if path.is_dir()), key=lambda item: item.name)
    if not snapshots:
        return model_name
    return str(snapshots[-1])


@lru_cache(maxsize=1)
def get_text_splitter() -> RecursiveCharacterTextSplitter:
    """统一构造 LangChain 文本切分器。

    `RecursiveCharacterTextSplitter` 是 LangChain 里最常用、最稳妥的通用切分器。
    它会优先按较大的语义分隔符切，再逐步退化到更细粒度字符级切分，
    对中英文混排、Markdown、普通说明文档都比较友好。
    """

    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=[
            "\n\n",
            "\n",
            "。",
            "！",
            "？",
            "；",
            "，",
            " ",
            "",
        ],
        keep_separator=False,
    )


@lru_cache(maxsize=1)
def get_embeddings() -> SentenceTransformerEmbeddings:
    return SentenceTransformerEmbeddings(EMBEDDING_MODEL_NAME)


@lru_cache(maxsize=1)
def get_vector_store() -> Chroma:
    """统一构造 LangChain Chroma 向量库实例。"""

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return Chroma(
        collection_name=CHROMA_COLLECTION_NAME,
        persist_directory=str(CHROMA_DIR),
        embedding_function=get_embeddings(),
        collection_metadata={"hnsw:space": "cosine"},
    )


def build_langchain_documents(
    *,
    file_path: str | Path,
    base_metadata: dict[str, Any] | None = None,
) -> list[LCDocument]:
    """完整执行 LangChain 的“加载 -> 切分”。

    返回值是切分后的 LangChain Document 列表，后续由业务层补充数据库主键、
    chunk 序号、Chroma id 等项目内元数据。
    """

    loader = StructuredDocumentLoader(file_path)
    loaded_documents = loader.load()
    if not loaded_documents:
        return []

    splitter = get_text_splitter()
    split_documents = splitter.split_documents(loaded_documents)

    if not base_metadata:
        return split_documents

    merged_documents: list[LCDocument] = []
    for item in split_documents:
        metadata = dict(base_metadata)
        metadata.update(item.metadata)
        merged_documents.append(LCDocument(page_content=item.page_content, metadata=metadata))
    return merged_documents


def annotate_split_documents(documents: list[LCDocument]) -> list[LCDocument]:
    """补齐每个 section 内部的 piece 序号信息。

    LangChain 的切分器只负责把文本切开，不知道业务上“这属于第几个子片段”。
    这里根据 `section_id` 分组后回填 `piece_index / piece_count`，方便前端和后续调试。
    """

    if not documents:
        return []

    counter = Counter(str(item.metadata.get("section_id") or "unknown") for item in documents)
    current_index: dict[str, int] = {}
    annotated: list[LCDocument] = []

    for item in documents:
        section_id = str(item.metadata.get("section_id") or "unknown")
        piece_index = current_index.get(section_id, 0)
        current_index[section_id] = piece_index + 1

        metadata = dict(item.metadata)
        metadata["piece_index"] = piece_index
        metadata["piece_count"] = counter[section_id]
        annotated.append(LCDocument(page_content=item.page_content, metadata=metadata))

    return annotated


def chroma_upsert(ids: list[str], documents: list[str], metadatas: list[dict[str, Any]]) -> None:
    """通过 LangChain Chroma 执行 upsert。

    LangChain 的公开接口以 `add_documents` 为主，没有直接暴露标准化 upsert。
    这里通过底层 collection 执行 upsert，但仍然由 LangChain 持有向量库对象，
    保证向量化、持久化和检索调用都在同一套 LangChain 运行时中完成。
    """

    if not ids:
        return
    vector_store = get_vector_store()
    # Go through the configured embedding model explicitly. Calling the raw
    # Chroma collection with only `documents` lets Chroma fall back to its
    # default embedding function, which can silently create a collection with
    # the wrong vector dimension.
    embeddings = get_embeddings().embed_documents(documents)
    vector_store._collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embeddings,
    )


def chroma_delete(ids: list[str]) -> None:
    if not ids:
        return
    get_vector_store().delete(ids=ids)


def chroma_query(query: str, top_k: int) -> list[tuple[LCDocument, float]]:
    if not query.strip():
        return []
    return get_vector_store().similarity_search_with_score(query, k=top_k)


def get_chat_llm(
    *,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
) -> ChatOpenAI:
    """返回 LangChain 的 ChatOpenAI 实例。

    MiniMax 当前走 OpenAI 兼容协议，因此直接使用 `ChatOpenAI` 即可。
    这样 Prompt、Runnable、流式输出都能走 LangChain 标准能力。
    """

    if not OPENAI_API_KEY:
        raise MinimaxConfigError(
            "Missing OPENAI_API_KEY or MINIMAX_API_KEY. Set it before starting the service."
        )

    model_kwargs: dict[str, Any] = {}
    if MINIMAX_REASONING_SPLIT:
        model_kwargs["extra_body"] = {"reasoning_split": True}

    return ChatOpenAI(
        model=MINIMAX_MODEL_NAME,
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
        temperature=MINIMAX_TEMPERATURE if temperature is None else temperature,
        max_tokens=MINIMAX_MAX_OUTPUT_TOKENS if max_output_tokens is None else max_output_tokens,
        model_kwargs=model_kwargs,
        streaming=True,
    )


def serialize_langchain_document(item: LCDocument) -> dict[str, Any]:
    return {
        "page_content": item.page_content,
        "metadata": json.loads(json.dumps(item.metadata, ensure_ascii=False)),
    }
