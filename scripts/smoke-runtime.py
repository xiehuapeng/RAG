"""Live smoke using disposable documents/sessions; never print credentials or answers."""
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZipFile

import requests


def docx_bytes(text: str) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                         '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                         '</Types>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                         '</Relationships>')
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                         f'<w:body><w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p></w:body></w:document>')
    return output.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--skip-qa", action="store_true", help="Skip the live LLM/SSE request")
    args = parser.parse_args()
    client = requests.Session()
    client.trust_env = False
    base = args.base_url.rstrip("/")
    report = {}
    sessions = []
    documents = []
    logged_in = False

    def post(path, **kwargs):
        response = client.post(base + path, timeout=180, **kwargs)
        response.raise_for_status()
        body = response.json()
        if body.get("code") != 0:
            raise RuntimeError(f"API failed: {path} code={body.get('code')}")
        return body.get("data")

    try:
        response = client.get(base + "/health", timeout=10)
        response.raise_for_status()
        assert response.json()["data"]["status"] == "ok"
        report["health"] = "ok"
        login = post("/api/auth/login", json={
            "username": os.getenv("QA_EVAL_USERNAME", "admin"),
            "password": os.getenv("QA_EVAL_PASSWORD", "admin123"),
        })
        client.headers["x-session-token"] = login["token"]
        logged_in = True
        marker = "smoke-" + uuid.uuid4().hex[:12]
        name = marker + ".docx"

        def upload(text, overwrite=False):
            result = post("/api/documents/upload", data={"overwrite": str(overwrite).lower()},
                          files={"file": (name, docx_bytes(text))})
            documents.append(result["id"])
            assert result["status"] == "ready"
            return result["id"]

        first_id = upload(f"CMIOT 物联网卡 {marker} 测试开户流程：这是一份虚构的自动验证文档。")
        current_id = upload(f"CMIOT 物联网卡 {marker} 测试开户流程：第一步校验测试环境，"
                            "第二步提交验证请求，第三步核对返回结果。仅用于自动验证，不是真实业务操作手册。", True)
        documents.remove(first_id)
        assert current_id != first_id
        report["upload_and_replace"] = "ready"
        chunks_before = post(f"/api/documents/{current_id}/chunks")
        response = client.post(base + "/api/documents/upload", data={"overwrite": "true"},
                               files={"file": (name, b"not a docx archive")}, timeout=30)
        assert response.status_code == 400
        assert post(f"/api/documents/{current_id}/chunks") == chunks_before
        assert post("/api/documents/upload/check", json={"file_name": name})["document"]["id"] == current_id
        report["invalid_overwrite_preserves_original"] = True

        session_id = post("/api/chat/sessions/create", json={"title": marker})["id"]
        sessions.append(session_id)
        if not args.skip_qa:
            started = time.perf_counter()
            events, deltas, final = [], [], None
            with client.post(base + f"/api/chat/sessions/{session_id}/messages/stream",
                             json={"content": f"CMIOT 物联网卡 {marker} 测试开户流程有哪些步骤？"},
                             stream=True, timeout=180) as response:
                response.raise_for_status()
                response.encoding = "utf-8"
                event = ""
                for line in response.iter_lines(decode_unicode=True):
                    if line.startswith("event:"):
                        event = line.partition(":")[2].strip()
                    elif line.startswith("data:"):
                        payload = json.loads(line.partition(":")[2])
                        events.append(event)
                        if event == "error":
                            raise RuntimeError("SSE error event")
                        if event == "delta":
                            deltas.append(payload)
                        if event == "end":
                            final = payload
            assert final is not None, "SSE did not finish"
            detail = post(f"/api/chat/sessions/{session_id}")
            answer = next(item for item in reversed(detail["messages"]) if item["role"] == "assistant")
            refs = answer.get("references") or []
            report["sse"] = {"seconds": round(time.perf_counter() - started, 2),
                             "events": sorted(set(events)), "delta_count": len(deltas),
                             "answer_chars": len(answer["content"]), "references": len(refs)}
            assert any(item.get("document_id") == current_id for item in refs), "Synthetic source not cited"
            assert answer["content"]
    finally:
        cleanup_errors = []
        for kind, ids in (("chat/sessions", sessions), ("documents", documents)):
            for item_id in ids:
                try:
                    post(f"/api/{kind}/{item_id}/delete")
                except Exception:
                    cleanup_errors.append({"kind": kind, "id": item_id})
        if logged_in:
            try:
                post("/api/auth/logout")
            except Exception:
                cleanup_errors.append({"kind": "auth/logout"})
        client.close()
        report["cleanup_errors"] = cleanup_errors
        print(json.dumps(report, ensure_ascii=False))
        if cleanup_errors:
            raise RuntimeError("Smoke cleanup incomplete; inspect reported test-only IDs")


if __name__ == "__main__":
    main()
