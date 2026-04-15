from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import MAX_UPLOAD_FILE_COUNT, MAX_UPLOAD_SIZE, SUPPORTED_EXTENSIONS, UPLOAD_DIR
from app.models import Chunk, Document
from app.services.langchain_runtime import annotate_split_documents, build_langchain_documents
from app.services.parsers import ParseError, ParsedSection, parse_document, parse_document_structure
from app.services.vector_store import delete_chunks, upsert_chunks


# 文档服务主流程集中在这个文件：
# - 校验上传
# - 保存原始文件
# - 解析文档结构
# - 生成 chunk
# - 写入数据库和向量库
@dataclass(slots=True)
class ChunkSpec:
    """项目内部的切块描述对象。

    LangChain 运行时里会先使用 `langchain_core.documents.Document` 表示中间结果，
    但数据库落库仍然沿用当前项目自己的字段结构，所以这里保留 `ChunkSpec`
    作为“LangChain 结果 -> 项目持久化模型”之间的桥接层。
    """

    content: str
    metadata: dict[str, Any]


def resolve_document_storage_path(document: Document, db: Session | None = None) -> Path:
    # 兼容旧数据里写死的绝对路径：优先用库里的路径，找不到时回退到当前 uploads 目录。
    raw_storage_path = (document.storage_path or "").strip()
    raw_path = Path(raw_storage_path)
    candidates: list[Path] = [raw_path]

    stored_name = ""
    for parser in (PureWindowsPath, PurePosixPath, Path):
        parsed_name = parser(raw_storage_path).name.strip() if raw_storage_path else ""
        if parsed_name:
            stored_name = parsed_name
            break
    if stored_name:
        candidates.append(UPLOAD_DIR / stored_name)

    original_name = Path(document.file_name).name.strip() if document.file_name else ""
    if original_name:
        candidates.append(UPLOAD_DIR / original_name)

    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        if not candidate.exists():
            continue

        if normalized != document.storage_path:
            document.storage_path = normalized
            if db is not None:
                db.add(document)
                db.commit()
                db.refresh(document)
        return candidate

    raise HTTPException(status_code=404, detail={"code": 3006, "message": "document source file not found"})


def validate_upload(file: UploadFile, payload: bytes) -> None:
    # 服务端兜底校验上传格式和体积，避免前端校验被绕过。
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail={
                "code": 3001,
                "message": "暂不支持该文件格式，请上传 txt、md、json、csv、docx、pdf、xls、xlsx 文件",
            },
        )
    if len(payload) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail={"code": 3002, "message": "file too large, max 200MB"})


def split_text(text: str) -> list[str]:
    """保留旧函数名，内部复用 LangChain 切分能力。"""

    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return []

    temp_path = UPLOAD_DIR / f"temp-split-{uuid.uuid4().hex}.txt"
    temp_path.write_text(normalized, encoding="utf-8")
    try:
        docs = build_langchain_documents(file_path=temp_path)
        return [item.page_content.strip() for item in docs if item.page_content.strip()]
    finally:
        temp_path.unlink(missing_ok=True)


def _build_chunk_metadata(document: Document, chunk: Chunk) -> dict[str, Any]:
    # 把 chunk 自身 metadata 与文档级信息合并，形成向量库最终使用的元数据。
    metadata = _load_chunk_metadata(chunk)
    metadata.update(
        {
            "document_id": document.id,
            "document_title": document.title,
            "file_name": document.file_name,
            "chunk_id": chunk.id,
            "chunk_index": chunk.chunk_index,
            "chroma_id": chunk.chroma_id,
        }
    )
    return metadata


def _load_chunk_metadata(chunk: Chunk) -> dict[str, Any]:
    # 数据库里是 JSON 字符串，这里统一转回 dict 供业务层使用。
    if not chunk.metadata_json:
        return {}
    try:
        return json.loads(chunk.metadata_json)
    except json.JSONDecodeError:
        return {}


def _sync_chunk_metadata_and_vectors(db: Session, document: Document) -> None:
    # 当 chunk 被重建、编辑或重排后，需要把数据库里的 metadata
    # 与向量库里的 documents / metadatas 一起同步。
    chroma_ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []

    for chunk in document.chunks:
        metadata = _build_chunk_metadata(document, chunk)
        chunk.metadata_json = json.dumps(metadata, ensure_ascii=False)
        chroma_ids.append(chunk.chroma_id)
        documents.append(chunk.content)
        metadatas.append(metadata)

    db.add(document)
    db.flush()
    upsert_chunks(chroma_ids, documents, metadatas)


def _build_chunk_specs(file_path: Path, full_text: str) -> list[ChunkSpec]:
    """LangChain 文档加载与文本切分入口。

    流程如下：

    1. 自定义 `StructuredDocumentLoader` 把文件解析成 LangChain Document 列表。
    2. `RecursiveCharacterTextSplitter` 把每个章节文档继续切成 embedding 友好的片段。
    3. 通过 `annotate_split_documents` 给每个 section 回填 `piece_index/piece_count`。
    4. 最后转换成项目自己的 `ChunkSpec`，供数据库和 Chroma 落库继续使用。
    """

    split_documents = annotate_split_documents(build_langchain_documents(file_path=file_path))
    specs: list[ChunkSpec] = []

    for item in split_documents:
        content = item.page_content.strip()
        if not content:
            continue
        specs.append(ChunkSpec(content=content, metadata=dict(item.metadata)))

    if specs:
        return specs

    if not full_text.strip():
        return []

    pieces = split_text(full_text)
    total = len(pieces)
    return [
        ChunkSpec(
            content=piece,
            metadata={
                "chapter_path": "正文",
                "section_title": "正文",
                "section_level": 1,
                "section_id": "fallback-body",
                "section_order": 1,
                "path_depth": 1,
                "piece_index": index,
                "piece_count": total,
            },
        )
        for index, piece in enumerate(pieces)
    ]


def _replace_document_chunks(db: Session, document: Document, chunk_specs: list[ChunkSpec]) -> None:
    # 文档重建索引时采用“全量替换”策略：
    # 先删旧向量，再清空旧 chunk，然后按最新切分结果整体重建。
    old_chroma_ids = [chunk.chroma_id for chunk in document.chunks if chunk.chroma_id]
    if old_chroma_ids:
        delete_chunks(old_chroma_ids)

    document.chunks.clear()
    db.flush()

    for index, spec in enumerate(chunk_specs):
        chunk = Chunk(
            chunk_index=index,
            chroma_id=f"doc-{document.id}-chunk-{index}-{uuid.uuid4().hex}",
            content=spec.content,
            metadata_json=json.dumps(spec.metadata, ensure_ascii=False),
        )
        document.chunks.append(chunk)

    db.add(document)
    db.flush()
    _sync_chunk_metadata_and_vectors(db, document)


def _section_to_outline(section: ParsedSection, ancestors: list[str] | None = None) -> dict[str, Any]:
    # 把解析器产出的章节树转换成前端更容易消费的大纲结构。
    ancestors = ancestors or []
    path_titles = ancestors + ([section.title] if section.title else [])
    return {
        "id": section.id,
        "label": section.title,
        "level": section.level,
        "path": " / ".join(path_titles),
        "content_count": sum(1 for block in section.blocks if block["type"] == "paragraph"),
        "children": [_section_to_outline(child, path_titles) for child in section.children],
    }


def get_document_or_404(db: Session, document_id: int) -> Document:
    # 统一封装文档查询和 404 异常，避免路由层重复写样板代码。
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail={"code": 3003, "message": "document not found"})
    return document


def get_chunk_or_404(db: Session, document_id: int, chunk_id: int) -> Chunk:
    # chunk 查询除了按主键，还会校验它确实属于当前 document。
    chunk = db.query(Chunk).filter(Chunk.id == chunk_id, Chunk.document_id == document_id).first()
    if not chunk:
        raise HTTPException(status_code=404, detail={"code": 3004, "message": "chunk not found"})
    return chunk


def check_existing_file(db: Session, file_name: str) -> Document | None:
    # 同名文件检查按最近更新时间排序，优先返回最新记录。
    return (
        db.query(Document)
        .filter(Document.file_name == file_name)
        .order_by(Document.updated_at.desc())
        .first()
    )


def ingest_document(db: Session, document: Document) -> Document:
    # 文档入库主流程：
    # pending -> processing -> ready/error
    # 上传后的解析、切块和向量索引都在这里完成。
    path = resolve_document_storage_path(document, db)
    document.status = "processing"
    document.error_message = None
    db.add(document)
    db.commit()

    try:
        structure = parse_document_structure(path)
        chunk_specs = _build_chunk_specs(path, structure.full_text)
        _replace_document_chunks(db, document, chunk_specs)
        document.chunk_count = len(chunk_specs)
        document.status = "ready"
        db.add(document)
        db.commit()
        db.refresh(document)
        return document
    except ParseError as exc:
        document.status = "error"
        document.error_message = str(exc)
        db.add(document)
        db.commit()
        raise HTTPException(status_code=400, detail={"code": 3003, "message": str(exc)}) from exc
    except Exception as exc:
        db.rollback()
        document.status = "error"
        document.error_message = str(exc)
        db.add(document)
        db.commit()
        raise HTTPException(status_code=500, detail={"code": 3003, "message": "document parse failed"}) from exc


def save_upload(
    db: Session,
    title: str,
    file: UploadFile,
    created_by: int,
    overwrite: bool = False,
) -> Document:
    # 上传保存阶段负责把原始文件落盘并创建 Document 记录，
    # 随后立即进入 ingest_document 做解析与索引构建。
    payload = file.file.read()
    validate_upload(file, payload)

    existing = check_existing_file(db, file.filename or "")
    if existing and not overwrite:
        raise HTTPException(status_code=409, detail={"code": 3005, "message": "file already exists"})
    if existing and overwrite:
        delete_document_with_vectors(db, existing)

    suffix = Path(file.filename or "").suffix.lower()
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    target = UPLOAD_DIR / stored_name
    target.write_bytes(payload)

    document = Document(
        title=title or Path(file.filename or stored_name).stem,
        file_name=file.filename or stored_name,
        storage_path=str(target),
        file_type=suffix.lstrip("."),
        file_size=len(payload),
        status="pending",
        created_by=created_by,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return ingest_document(db, document)


def save_upload_batch(
    db: Session,
    files: list[UploadFile],
    created_by: int,
    overwrite: bool = False,
) -> list[dict]:
    # 批量上传按文件逐个处理，单个失败不会中断整个批次。
    if len(files) > MAX_UPLOAD_FILE_COUNT:
        raise HTTPException(
            status_code=400,
            detail={"code": 3006, "message": f"一次最多上传 {MAX_UPLOAD_FILE_COUNT} 个文件"},
        )

    results: list[dict] = []
    for file in files:
        try:
            document = save_upload(db, "", file, created_by, overwrite=overwrite)
            results.append(
                {
                    "file_name": file.filename,
                    "status": document.status,
                    "document_id": document.id,
                    "message": "uploaded",
                }
            )
        except HTTPException as exc:
            message = exc.detail["message"] if isinstance(exc.detail, dict) else str(exc.detail)
            results.append(
                {
                    "file_name": file.filename,
                    "status": "error",
                    "document_id": None,
                    "message": message,
                }
            )
    return results


def get_document_content(document: Document, db: Session | None = None) -> str:
    # 实时从原始文件解析全文，确保预览内容与磁盘文件一致。
    return parse_document(resolve_document_storage_path(document, db))


def get_document_outline(document: Document, db: Session | None = None) -> list[dict[str, Any]]:
    # 大纲来自结构化解析结果，不依赖数据库里的 chunk。
    structure = parse_document_structure(resolve_document_storage_path(document, db))
    return [_section_to_outline(section) for section in structure.sections]


def rebuild_document_index(db: Session, document: Document) -> Document:
    # 当前“重建索引”本质上就是重新执行整个 ingest 流程。
    return ingest_document(db, document)


def update_chunk_content_and_metadata(
    db: Session,
    chunk: Chunk,
    content: str | None,
    metadata: dict,
) -> Chunk:
    # 人工编辑 chunk 后，需要同时更新数据库和向量库，避免检索结果仍然命中旧内容。
    document = chunk.document
    if content is not None:
        chunk.content = content.strip()
    merged_metadata = _load_chunk_metadata(chunk)
    merged_metadata.update(metadata)
    merged_metadata.setdefault("document_id", document.id)
    merged_metadata.setdefault("document_title", document.title)
    merged_metadata.setdefault("file_name", document.file_name)
    merged_metadata.setdefault("chunk_id", chunk.id)
    merged_metadata.setdefault("chunk_index", chunk.chunk_index)
    merged_metadata.setdefault("chroma_id", chunk.chroma_id)
    chunk.metadata_json = json.dumps(merged_metadata, ensure_ascii=False)

    db.add(chunk)
    db.commit()
    db.refresh(chunk)
    upsert_chunks([chunk.chroma_id], [chunk.content], [merged_metadata])
    return chunk


def delete_chunk_with_vector(db: Session, chunk: Chunk) -> None:
    # 删除单块后，还要重新整理剩余 chunk 的顺序并同步向量库 metadata。
    document = chunk.document
    delete_chunks([chunk.chroma_id])
    db.delete(chunk)
    db.flush()

    remaining_chunks = db.query(Chunk).filter(Chunk.document_id == document.id).order_by(Chunk.chunk_index.asc()).all()
    for index, item in enumerate(remaining_chunks):
        item.chunk_index = index
        db.add(item)

    document.chunk_count = len(remaining_chunks)
    db.add(document)
    db.flush()
    _sync_chunk_metadata_and_vectors(db, document)
    db.commit()


def _delete_stored_file(storage_path: str) -> None:
    # 磁盘文件删除失败时不阻断主流程，最多留下孤儿文件。
    path = Path(storage_path)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def delete_document_with_vectors(db: Session, document: Document) -> None:
    # 文档删除需要保证数据库、磁盘文件、向量库三处状态一起清理。
    chroma_ids = [chunk.chroma_id for chunk in document.chunks if chunk.chroma_id]
    if chroma_ids:
        delete_chunks(chroma_ids)
    _delete_stored_file(document.storage_path)
    db.delete(document)
    db.commit()


def backfill_vector_store(db: Session) -> None:
    # 启动时对历史数据做补偿：
    # 把旧 chunk / 旧 chroma_id / 缺失 metadata 的记录补成当前格式。
    ready_documents = db.query(Document).filter(Document.status == "ready").all()
    changed = False

    for document in ready_documents:
        needs_sync = any(
            not chunk.chroma_id
            or chunk.chroma_id.startswith("legacy-")
            or not chunk.metadata_json
            for chunk in document.chunks
        )
        needs_structure_rebuild = False
        if document.file_type == "docx":
            needs_structure_rebuild = any(
                "chapter_path" not in _load_chunk_metadata(chunk)
                for chunk in document.chunks
            )

        if needs_structure_rebuild:
            try:
                ingest_document(db, document)
                changed = True
                continue
            except Exception:
                # 老数据重建失败时不阻断启动流程，后续仍可手工重新建立索引。
                pass

        if not needs_sync:
            continue

        for chunk in document.chunks:
            if not chunk.chroma_id or chunk.chroma_id.startswith("legacy-"):
                chunk.chroma_id = f"doc-{document.id}-chunk-{chunk.chunk_index}-{uuid.uuid4().hex}"
        _sync_chunk_metadata_and_vectors(db, document)
        changed = True

    if changed:
        db.commit()
