from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import MAX_UPLOAD_SIZE, SUPPORTED_EXTENSIONS, UPLOAD_DIR
from app.models import Chunk, Document
from app.services.langchain_runtime import annotate_split_documents, build_langchain_documents
from app.services.parsers import ParseError, ParsedSection, parse_document, parse_document_structure
from app.services.vector_store import delete_chunks, upsert_chunks


@dataclass(slots=True)
class ChunkSpec:
    content: str
    metadata: dict[str, Any]


def validate_upload(file: UploadFile, payload: bytes) -> None:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail={"code": 3001, "message": "unsupported file format"})
    if len(payload) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail={"code": 3002, "message": "file too large, max 200MB"})


def split_text(text: str) -> list[str]:
    """旧接口保留，内部改为复用 LangChain 的 TextSplitter。"""

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
    if not chunk.metadata_json:
        return {}
    try:
        return json.loads(chunk.metadata_json)
    except json.JSONDecodeError:
        return {}


def _sync_chunk_metadata_and_vectors(db: Session, document: Document) -> None:
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


def _compose_chunk_content(path_titles: list[str], body_text: str) -> str:
    prefix = " / ".join(path_titles).strip()
    if prefix:
        return f"{prefix}\n\n{body_text.strip()}".strip()
    return body_text.strip()


def _split_section_body(body_text: str) -> list[str]:
    normalized = body_text.replace("\r\n", "\n").strip()
    if not normalized:
        return []
    paragraphs = [part.strip() for part in normalized.split("\n") if part.strip()]
    if not paragraphs:
        return []
    return ["\n".join(paragraphs).strip()]

def _extract_heading_number(title: str) -> tuple[int, ...] | None:
    match = _HEADING_NUMBER_RE.match(title or "")
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _select_numeric_split_children(section: ParsedSection) -> list[ParsedSection]:
    # 题号优先：父章节有数字子题时，只把这些数字子题当作切点。
    section_number = _extract_heading_number(section.title)
    numeric_children: list[tuple[ParsedSection, tuple[int, ...]]] = []

    for child in section.children:
        child_number = _extract_heading_number(child.title)
        if not child_number:
            continue
        numeric_children.append((child, child_number))

    if section_number is None:
        if not numeric_children:
            return []
        min_depth = min(len(number) for _, number in numeric_children)
        return [child for child, number in numeric_children if len(number) == min_depth]

    split_children: list[ParsedSection] = []
    for child, child_number in numeric_children:
        if len(child_number) == len(section_number) + 1 and child_number[: len(section_number)] == section_number:
            split_children.append(child)

    return split_children


def _collect_section_body_text(section: ParsedSection, skip_child_ids: set[str] | None = None) -> str:
    texts: list[str] = []
    skip_child_ids = skip_child_ids or set()

    for block in section.blocks:
        if block["type"] == "paragraph":
            text = str(block.get("text", "")).strip()
            if text:
                texts.append(text)
            continue

        if block["type"] == "section":
            child = block["node"]
            if child.id in skip_child_ids:
                continue
            child_text = _collect_section_body_text(child, skip_child_ids)
            if child_text:
                texts.append(child_text)

    return "\n".join(texts).strip()


def _collect_chunk_specs(section: ParsedSection, ancestors: list[str]) -> list[ChunkSpec]:
    path_titles = ancestors + ([section.title] if section.title else [])
    split_children = _select_numeric_split_children(section)
    split_child_ids = {child.id for child in split_children}
    body_text = _collect_section_body_text(section, split_child_ids)

    specs: list[ChunkSpec] = []
    if body_text:
        section_pieces = _split_section_body(body_text)
        for index, piece in enumerate(section_pieces):
            content = _compose_chunk_content(path_titles, piece)
            specs.append(
                ChunkSpec(
                    content=content,
                    metadata={
                        "chapter_path": " / ".join(path_titles),
                        "section_title": section.title,
                        "section_level": section.level,
                        "section_id": section.id,
                        "section_order": section.order_index,
                        "path_depth": len(path_titles),
                        "piece_index": index,
                        "piece_count": len(section_pieces),
                    },
                )
            )

    for child in split_children:
        specs.extend(_collect_chunk_specs(child, path_titles))

    return specs


def _build_chunk_specs(structure_sections: list[ParsedSection], full_text: str) -> list[ChunkSpec]:
    specs: list[ChunkSpec] = []
    for section in structure_sections:
        specs.extend(_collect_chunk_specs(section, []))

    if not specs and full_text.strip():
        for index, piece in enumerate(split_text(full_text)):
            specs.append(
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
                        "piece_count": 1,
                    },
                )
            )
    return specs


def _replace_document_chunks(db: Session, document: Document, chunk_specs: list[ChunkSpec]) -> None:
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
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail={"code": 3003, "message": "document not found"})
    return document


def get_chunk_or_404(db: Session, document_id: int, chunk_id: int) -> Chunk:
    chunk = db.query(Chunk).filter(Chunk.id == chunk_id, Chunk.document_id == document_id).first()
    if not chunk:
        raise HTTPException(status_code=404, detail={"code": 3004, "message": "chunk not found"})
    return chunk


def check_existing_file(db: Session, file_name: str) -> Document | None:
    return (
        db.query(Document)
        .filter(Document.file_name == file_name)
        .order_by(Document.updated_at.desc())
        .first()
    )


def ingest_document(db: Session, document: Document) -> Document:
    path = Path(document.storage_path)
    document.status = "processing"
    document.error_message = None
    db.add(document)
    db.commit()

    try:
        structure = parse_document_structure(path)
        chunk_specs = _build_chunk_specs(structure.sections, structure.full_text)
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


def get_document_content(document: Document) -> str:
    return parse_document(Path(document.storage_path))


def get_document_outline(document: Document) -> list[dict[str, Any]]:
    structure = parse_document_structure(Path(document.storage_path))
    return [_section_to_outline(section) for section in structure.sections]


def rebuild_document_index(db: Session, document: Document) -> Document:
    return ingest_document(db, document)


def update_chunk_content_and_metadata(
    db: Session,
    chunk: Chunk,
    content: str | None,
    metadata: dict,
) -> Chunk:
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
    path = Path(storage_path)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def delete_document_with_vectors(db: Session, document: Document) -> None:
    chroma_ids = [chunk.chroma_id for chunk in document.chunks if chunk.chroma_id]
    if chroma_ids:
        delete_chunks(chroma_ids)
    _delete_stored_file(document.storage_path)
    db.delete(document)
    db.commit()


def backfill_vector_store(db: Session) -> None:
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
                # 旧文档重建失败不阻断启动，保留原有记录，后续可手动重建。
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
