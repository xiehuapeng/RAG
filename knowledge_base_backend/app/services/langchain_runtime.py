from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from langchain_chroma import Chroma
from langchain_community.document_loaders.base import BaseLoader
from langchain_core.documents import Document as LCDocument
from langchain_core.embeddings import Embeddings
from langchain_openai import ChatOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import CrossEncoder, SentenceTransformer

from app.config import (
    CHROMA_COLLECTION_NAME,
    CHROMA_DIR,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    EMBEDDING_MODEL_NAME,
    LLM_MAX_OUTPUT_TOKENS,
    LLM_MODEL_NAME,
    LLM_NO_THINK,
    LLM_REASONING_SPLIT,
    LLM_TEMPERATURE,
    LLM_THINKING_BUDGET,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    RERANKER_MODEL_NAME,
)
from app.services.llm_client import LLMConfigError, _strip_proxy_env
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

        分片策略：
        1. 若整个章节（含所有子章节）的正文不超过 CHUNK_SIZE，则合并为一个 chunk，
           避免“前置条件/操作步骤”这类多小条的章节被切得太碎、检索时召回不全。
        2. 若超过 CHUNK_SIZE，则把相邻的短子章节按累计长度合并成接近 CHUNK_SIZE 的块，
           而不是每个子章节独立成一个 chunk。
        """

        path_titles = ancestors + ([section.title] if section.title else [])
        full_body = self._collect_section_body(section)

        if not full_body:
            for child in section.children:
                yield from self._yield_section_documents(child, path_titles)
            return

        if len(full_body) <= CHUNK_SIZE:
            yield self._make_document(path_titles, section, full_body)
            return

        yield from self._yield_merged_children(section, path_titles)

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
                child_title = str(child.title or "").strip()
                child_text = StructuredDocumentLoader._collect_section_body(child)
                parts: list[str] = []
                if child_title:
                    parts.append(child_title)
                if child_text:
                    parts.append(child_text)
                if parts:
                    texts.append("\n".join(parts))
        return "\n".join(texts).strip()

    @staticmethod
    def _collect_direct_body(section: ParsedSection) -> str:
        # 只收集 section 自身的段落正文，不递归子章节。
        texts: list[str] = []
        for block in section.blocks:
            if block["type"] == "paragraph":
                text = str(block.get("text", "")).strip()
                if text:
                    texts.append(text)
        return "\n".join(texts).strip()

    def _make_document(self, path_titles: list[str], section: ParsedSection, body_text: str) -> LCDocument:
        return LCDocument(
            page_content=self._compose_chunk_content(path_titles, body_text),
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

    def _make_merged_document(self, path_titles: list[str], section: ParsedSection, body_parts: list[str]) -> LCDocument:
        return self._make_document(path_titles, section, "\n\n".join(body_parts))

    def _yield_merged_children(self, section: ParsedSection, path_titles: list[str]) -> Iterator[LCDocument]:
        # 父章节自身的直接正文单独成 chunk（不含子章节）。
        direct_body = self._collect_direct_body(section)
        if direct_body:
            yield self._make_document(path_titles, section, direct_body)

        # 相邻短子章节按累计长度合并，逼近 CHUNK_SIZE 时断开。
        buffer_parts: list[str] = []
        buffer_len = 0
        for child in section.children:
            child_title = str(child.title or "").strip()
            child_body = self._collect_section_body(child)
            # 纯标题小节（无正文，例如"3.3 集团基本信息展示"）也要保留，
            # 否则检索时会把这类步骤/前置条件整条漏掉。
            if not child_body and not child_title:
                continue

            if len(child_body) > CHUNK_SIZE:
                if buffer_parts:
                    yield self._make_merged_document(path_titles, section, buffer_parts)
                    buffer_parts = []
                    buffer_len = 0
                yield from self._yield_section_documents(child, path_titles)
                continue

            if child_title and child_body:
                piece = f"{child_title}\n{child_body}"
            elif child_title:
                piece = child_title
            else:
                piece = child_body
            buffer_parts.append(piece)
            buffer_len += len(piece)
            if buffer_len >= CHUNK_SIZE:
                yield self._make_merged_document(path_titles, section, buffer_parts)
                buffer_parts = []
                buffer_len = 0

        if buffer_parts:
            yield self._make_merged_document(path_titles, section, buffer_parts)


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
def get_reranker() -> CrossEncoder:
    """返回交叉编码器重排模型（进程级单例）。

    与 Bi-Encoder（embedding）各自独立编码不同，Cross-Encoder 把
    (query, chunk) 拼接后联合输入 Transformer，能捕捉两者的细粒度交互特征，
    适合对召回候选做精排，弥补“问句措辞 vs 章节标题”的语义鸿沟。
    这里同样优先解析本地缓存快照并强制 `local_files_only=True`，
    避免运行期访问 HuggingFace Hub 元数据检查导致检索请求被阻塞。
    """

    resolved_model = resolve_sentence_transformer_path(RERANKER_MODEL_NAME)
    # max_length=512 覆盖 bge-reranker-base 的训练长度；
    # num_labels=1 时默认激活函数为 Sigmoid，predict 输出即 [0,1] 相关性概率。
    return CrossEncoder(
        resolved_model,
        max_length=512,
        device="cpu",
        local_files_only=True,
    )


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

    LLM 当前走 OpenAI 兼容协议，因此直接使用 `ChatOpenAI` 即可。
    这样 Prompt、Runnable、流式输出都能走 LangChain 标准能力。
    """

    if not OPENAI_API_KEY:
        raise LLMConfigError(
            "Missing OPENAI_API_KEY or LLM_API_KEY. Set it before starting the service."
        )

    model_kwargs: dict[str, Any] = {}
    extra_body: dict[str, Any] = {}
    if LLM_REASONING_SPLIT:
        extra_body["reasoning_split"] = True
    if LLM_NO_THINK:
        # DeepSeek 网关不支持 enable_thinking；用 thinking.type=disabled 关闭思考链，
        # 可让首字延迟从分钟级降到秒级。
        extra_body["thinking"] = {"type": "disabled"}
    elif LLM_THINKING_BUDGET > 0:
        extra_body["thinking"] = {"type": "enabled", "budget_tokens": LLM_THINKING_BUDGET}
    if extra_body:
        model_kwargs["extra_body"] = extra_body

    # 清除进程内代理环境变量，并构造一个显式禁用代理的 httpx 客户端，
    # 避免系统级 HTTP_PROXY（Clash Verge 7897 端口）关闭后连接被拒绝。
    _strip_proxy_env()
    http_client = httpx.Client(
        timeout=httpx.Timeout(60.0, connect=10.0),
        proxy=None,
        transport=httpx.HTTPTransport(retries=2),
    )

    return ChatOpenAI(
        model=LLM_MODEL_NAME,
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
        temperature=LLM_TEMPERATURE if temperature is None else temperature,
        max_tokens=LLM_MAX_OUTPUT_TOKENS if max_output_tokens is None else max_output_tokens,
        model_kwargs=model_kwargs,
        streaming=True,
        http_client=http_client,
    )


def serialize_langchain_document(item: LCDocument) -> dict[str, Any]:
    return {
        "page_content": item.page_content,
        "metadata": json.loads(json.dumps(item.metadata, ensure_ascii=False)),
    }
