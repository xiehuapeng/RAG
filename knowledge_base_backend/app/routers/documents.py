from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_admin
from app.models import Document
from app.responses import success
from app.schemas import ChunkUpdateRequest, DocumentListRequest, DocumentUploadCheckRequest
from app.services.documents_langchain import (
    check_existing_file,
    delete_chunk_with_vector,
    delete_document_with_vectors,
    get_chunk_or_404,
    get_document_content,
    get_document_or_404,
    get_document_outline,
    rebuild_document_index,
    save_upload,
    save_upload_batch,
    update_chunk_content_and_metadata,
)


router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("/upload")
def upload_document(
    title: str = Form(""),
    file: UploadFile = File(...),
    overwrite: bool = Form(False),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    # 单文件上传完整触发“落盘 -> 解析 -> 切块 -> 向量索引”流程。
    document = save_upload(db, title, file, user.id, overwrite=overwrite)
    return success({"id": document.id, "title": document.title, "status": document.status})


@router.post("/upload/check")
def check_upload(payload: DocumentUploadCheckRequest, db: Session = Depends(get_db), user=Depends(require_admin)):
    # 上传前预检查同名文件，给前端决定是否覆盖提供依据。
    existing = check_existing_file(db, payload.file_name)
    return success(
        {
            "exists": bool(existing),
            "document": {
                "id": existing.id,
                "title": existing.title,
                "file_name": existing.file_name,
                "status": existing.status,
                "updated_at": existing.updated_at,
            }
            if existing
            else None,
        }
    )


@router.post("/upload/batch")
def upload_document_batch(
    files: list[UploadFile] = File(...),
    overwrite: bool = Form(False),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    # 批量上传内部复用单文件逻辑，但每个文件的结果独立返回。
    return success(save_upload_batch(db, files, user.id, overwrite=overwrite))


@router.post("")
def list_documents(
    payload: DocumentListRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    # 文档列表支持按标题关键字和状态筛选，并返回分页信息。
    query = db.query(Document)
    if payload.keyword:
        query = query.filter(Document.title.like(f"%{payload.keyword}%"))
    if payload.status:
        query = query.filter(Document.status == payload.status)

    total = query.count()
    rows = (
        query.order_by(Document.created_at.desc())
        .offset((payload.page_num - 1) * payload.page_size)
        .limit(payload.page_size)
        .all()
    )
    data = [
        {
            "id": row.id,
            "title": row.title,
            "file_name": row.file_name,
            "file_type": row.file_type,
            "file_size": row.file_size,
            "status": row.status,
            "chunk_count": row.chunk_count,
            "created_at": row.created_at,
        }
        for row in rows
    ]
    return success(data, total=total, page_num=payload.page_num, page_size=payload.page_size)


@router.post("/{document_id}")
def get_document(document_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    # 文档详情会返回状态、错误信息和存储路径，便于后台排查问题。
    row = get_document_or_404(db, document_id)
    return success(
        {
            "id": row.id,
            "title": row.title,
            "file_name": row.file_name,
            "file_type": row.file_type,
            "file_size": row.file_size,
            "status": row.status,
            "error_message": row.error_message,
            "chunk_count": row.chunk_count,
            "created_by": row.created_by,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "storage_path": row.storage_path,
        }
    )


@router.post("/{document_id}/delete")
def delete_document(document_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    # 删除文档时会同步删除数据库记录、向量索引和磁盘文件。
    row = get_document_or_404(db, document_id)
    delete_document_with_vectors(db, row)
    return success(message="deleted")


@router.post("/{document_id}/chunks")
def get_document_chunks(document_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    # 返回切块后的正文和 metadata，主要供管理端调试和人工修订使用。
    row = get_document_or_404(db, document_id)
    chunks = []
    for chunk in row.chunks:
        chunks.append(
            {
                "id": chunk.id,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "metadata": json.loads(chunk.metadata_json) if chunk.metadata_json else None,
            }
        )
    return success(chunks)


@router.post("/{document_id}/content")
def get_document_content_api(document_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    # 读取解析后的全文内容，适合前端做原文预览。
    document = get_document_or_404(db, document_id)
    return success({"content": get_document_content(document)})


@router.post("/{document_id}/outline")
def get_document_outline_api(document_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    # 返回文档结构化大纲，供前端树形目录展示。
    document = get_document_or_404(db, document_id)
    return success(get_document_outline(document))


@router.post("/{document_id}/reindex")
def rebuild_document_index_api(document_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    # 重建索引会重新执行解析、切块和向量写入。
    document = get_document_or_404(db, document_id)
    rebuilt = rebuild_document_index(db, document)
    return success({"id": rebuilt.id, "status": rebuilt.status, "chunk_count": rebuilt.chunk_count})


@router.post("/{document_id}/resplit")
def resplit_document_api(document_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    # 当前 resplit 与 reindex 走同一条实现路径，语义上更偏“重新切块”。
    document = get_document_or_404(db, document_id)
    rebuilt = rebuild_document_index(db, document)
    return success({"id": rebuilt.id, "status": rebuilt.status, "chunk_count": rebuilt.chunk_count})


@router.post("/{document_id}/chunks/{chunk_id}/update")
def update_document_chunk(
    document_id: int,
    chunk_id: int,
    payload: ChunkUpdateRequest,
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    # 允许管理员手工修改单个 chunk 的正文和 metadata，并立即同步向量库。
    chunk = get_chunk_or_404(db, document_id, chunk_id)
    updated = update_chunk_content_and_metadata(db, chunk, payload.content, payload.metadata)
    return success(
        {
            "id": updated.id,
            "chunk_index": updated.chunk_index,
            "content": updated.content,
            "metadata": json.loads(updated.metadata_json) if updated.metadata_json else None,
        }
    )


@router.post("/{document_id}/chunks/{chunk_id}/delete")
def delete_document_chunk(
    document_id: int,
    chunk_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    # 删除某个 chunk 后，会同步重排剩余块的顺序编号。
    chunk = get_chunk_or_404(db, document_id, chunk_id)
    delete_chunk_with_vector(db, chunk)
    return success(message="chunk deleted")
