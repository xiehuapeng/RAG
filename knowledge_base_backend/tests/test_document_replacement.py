from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Chunk, Document, User
from app.services import documents_langchain as service


@pytest.fixture
def replacement(tmp_path, monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(username="test", password_hash="unused", role="admin")
        db.add(user)
        db.flush()
        old_file = tmp_path / "old.docx"
        old_file.write_bytes(b"original source")
        old = Document(title="original", file_name="manual.docx", file_type="docx",
                       storage_path=str(old_file), status="ready", chunk_count=1, created_by=user.id)
        old.chunks.append(Chunk(chunk_index=0, chroma_id="old-vector", content="original evidence"))
        db.add(old)
        db.commit()
        old_id, user_id = old.id, user.id
        vectors = {"old-vector": "original evidence"}
        def upsert(ids, texts, metadata):
            vectors.update(zip(ids, texts))
        def delete(ids):
            for key in ids:
                vectors.pop(key, None)
        monkeypatch.setattr(service, "UPLOAD_DIR", tmp_path)
        monkeypatch.setattr(service, "upsert_chunks", upsert)
        monkeypatch.setattr(service, "delete_chunks", delete)
        monkeypatch.setattr(service, "invalidate_keyword_cache", Mock())
        monkeypatch.setattr(service, "parse_document_structure", lambda path: SimpleNamespace(full_text="new content"))
        monkeypatch.setattr(service, "_build_chunk_specs", lambda *args: [
            service.ChunkSpec("new evidence", {}), service.ChunkSpec("more evidence", {}),
        ])
        yield SimpleNamespace(db=db, old_id=old_id, user_id=user_id, old_file=old_file,
                              vectors=vectors, folder=tmp_path)
    engine.dispose()


def upload(case):
    return service.save_upload(case.db, "replacement", UploadFile(filename="manual.docx", file=BytesIO(b"new")),
                               case.user_id, overwrite=True)


def assert_original_intact(case):
    old = case.db.get(Document, case.old_id)
    assert old.status == "ready"
    assert [c.content for c in old.chunks] == ["original evidence"]
    assert case.db.query(Document).count() == 1
    assert case.db.query(Chunk).count() == 1
    assert case.old_file.read_bytes() == b"original source"
    assert case.vectors == {"old-vector": "original evidence"}
    assert list(case.folder.iterdir()) == [case.old_file]


def test_parse_failure_keeps_original(replacement, monkeypatch):
    monkeypatch.setattr(service, "parse_document_structure", Mock(side_effect=service.ParseError("invalid docx")))
    with pytest.raises(HTTPException) as error:
        upload(replacement)
    assert error.value.status_code == 400
    assert_original_intact(replacement)


def test_empty_replacement_keeps_original(replacement, monkeypatch):
    monkeypatch.setattr(service, "_build_chunk_specs", lambda *args: [])
    with pytest.raises(HTTPException):
        upload(replacement)
    assert_original_intact(replacement)


def test_partial_vector_failure_compensates_only_new_ids(replacement, monkeypatch):
    def fail(ids, texts, metadata):
        replacement.vectors[ids[0]] = texts[0]
        raise RuntimeError("injected partial vector failure")
    monkeypatch.setattr(service, "upsert_chunks", fail)
    with pytest.raises(HTTPException) as error:
        upload(replacement)
    assert error.value.status_code == 500
    assert_original_intact(replacement)


def test_commit_failure_restores_sql_and_vectors(replacement, monkeypatch):
    monkeypatch.setattr(replacement.db, "commit", Mock(side_effect=RuntimeError("injected commit failure")))
    with pytest.raises(HTTPException):
        upload(replacement)
    assert_original_intact(replacement)


def test_success_switches_only_after_new_vectors_are_ready(replacement, monkeypatch):
    def upsert(ids, texts, metadata):
        assert replacement.old_file.exists()
        assert "old-vector" in replacement.vectors
        assert replacement.db.get(Document, replacement.old_id).status == "ready"
        assert all(item["chunk_id"] for item in metadata)
        replacement.vectors.update(zip(ids, texts))
    monkeypatch.setattr(service, "upsert_chunks", upsert)
    new = upload(replacement)
    assert new.status == "ready" and new.chunk_count == 2
    assert new.id != replacement.old_id
    assert replacement.db.get(Document, replacement.old_id) is None
    assert replacement.db.query(Document).count() == 1
    assert replacement.db.query(Chunk).count() == 2
    assert not replacement.old_file.exists()
    assert "old-vector" not in replacement.vectors
    assert set(replacement.vectors) == {chunk.chroma_id for chunk in new.chunks}
    service.invalidate_keyword_cache.assert_called_once()


def test_old_vector_cleanup_failure_does_not_undo_commit(replacement, monkeypatch, caplog):
    monkeypatch.setattr(service, "delete_chunks", Mock(side_effect=RuntimeError("cleanup unavailable")))
    new = upload(replacement)
    assert new.status == "ready"
    assert replacement.db.get(Document, replacement.old_id) is None
    assert "cleanup required" in caplog.text


def test_non_overwrite_conflict_does_not_touch_original(replacement):
    with pytest.raises(HTTPException) as error:
        service.save_upload(replacement.db, "new", UploadFile(filename="manual.docx", file=BytesIO(b"new")),
                            replacement.user_id, overwrite=False)
    assert error.value.status_code == 409
    assert_original_intact(replacement)


def test_replacement_cleans_old_source_after_workspace_move(replacement):
    old = replacement.db.get(Document, replacement.old_id)
    old.storage_path = r"C:\missing-workspace\uploads\old.docx"
    replacement.db.commit()
    new = upload(replacement)
    assert new.status == "ready"
    assert not replacement.old_file.exists()
