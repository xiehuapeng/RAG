"""端到端验证重启后的问答全流程。

流程：健康检查 -> 登录 -> 创建会话 -> SSE 流式问答 -> 校验事件序列、证据与回答。
问题从 qa_eval 用例 JSON 中读取，避免在终端内联中文；结果以 UTF-8 JSON 落盘。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

BASE_URL = "http://127.0.0.1:8000"
REPORT_PATH = Path(__file__).resolve().parent.parent / "logs" / "qa_flow_verify_report.json"
CASES_PATH = (
    Path(__file__).resolve().parent.parent
    / "knowledge_base_backend"
    / "tests"
    / "qa_eval"
    / "qa_test_cases_3_first20.json"
)

report: dict = {"steps": []}


def record(step: str, ok: bool, detail: dict) -> None:
    report["steps"].append({"step": step, "ok": ok, **detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {step}")
    if not ok:
        finish(overall_ok=False)
        sys.exit(1)


def finish(overall_ok: bool) -> None:
    report["overall_ok"] = overall_ok
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    print(f"Report written to {REPORT_PATH}")


def load_question() -> str:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    first = cases[0] if isinstance(cases, list) else list(cases.values())[0]
    return first["question"]


def main() -> None:
    t_script_start = time.time()

    # 1. 健康检查
    r = requests.get(f"{BASE_URL}/health", timeout=15)
    record("health", r.status_code == 200, {"status": r.status_code, "body": r.text[:200]})

    # 2. 登录
    t0 = time.time()
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": "admin", "password": "admin123"},
        timeout=30,
    )
    login_seconds = round(time.time() - t0, 2)
    data = r.json()
    token = (data.get("data") or {}).get("token") or (data.get("data") or {}).get("session_token")
    record("login", r.status_code == 200 and data.get("code") == 0 and bool(token), {"data_keys": list((data.get("data") or {}).keys()), "seconds": login_seconds})
    headers = {"x-session-token": token}

    # 3. 创建会话
    t0 = time.time()
    r = requests.post(f"{BASE_URL}/api/chat/sessions/create", json={"title": "flow-verify"}, headers=headers, timeout=30)
    create_session_seconds = round(time.time() - t0, 2)
    data = r.json()
    session_id = (data.get("data") or {}).get("session_id") or (data.get("data") or {}).get("id")
    record("create_session", r.status_code == 200 and bool(session_id), {"session_id": session_id, "seconds": create_session_seconds})

    # 4. SSE 流式问答
    question = load_question()
    report["question"] = question
    started = time.time()
    events: list[dict] = []
    answer_text = ""
    end_answer = ""
    evidence_count = 0
    timeline: list[tuple[float, str]] = []
    current_event = "message"
    with requests.post(
        f"{BASE_URL}/api/chat/sessions/{session_id}/messages/stream",
        json={"content": question},
        headers=headers,
        stream=True,
        timeout=(15, 600),
    ) as resp:
        if resp.status_code != 200:
            record("stream_request", False, {"status": resp.status_code})
        current_data_lines: list[str] = []
        for raw in resp.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            line = raw.strip()
            if line.startswith("event:"):
                current_event = line.split(":", 1)[1].strip()
                continue
            if line.startswith("data:"):
                current_data_lines.append(line.split(":", 1)[1].strip())
                continue
            if line == "" and current_data_lines:
                payload = "\n".join(current_data_lines)
                current_data_lines = []
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    obj = {"raw": payload}
                event_type = obj.get("type") or current_event
                timeline.append((round(time.time() - started, 2), event_type))
                events.append({"event": event_type, "data": obj if len(payload) < 400 else {"keys": list(obj.keys())}})
                print(f"  <SSE event: {event_type} @ {timeline[-1][0]}s>", flush=True)
                if event_type == "delta":
                    answer_text += obj.get("content") or obj.get("delta") or ""
                if event_type == "evidence":
                    evidence_count = len(obj.get("chunks") or obj.get("evidence") or [])
                if event_type == "end":
                    end_answer = obj.get("answer") or ""
                    report["end_event_keys"] = list(obj.keys())

    elapsed = round(time.time() - started, 2)
    event_types = [e["event"] for e in events]
    report["event_types"] = event_types
    report["elapsed_seconds"] = elapsed

    expected_sequence = ["start", "progress", "understanding", "evidence", "end"]
    seq_ok = all(t in event_types for t in expected_sequence)
    record(
        "stream_sequence",
        seq_ok,
        {"expected_contains": expected_sequence, "actual": event_types, "elapsed_seconds": elapsed},
    )

    # 5. 回答质量基础校验（以 end 事件中的完整回答为准，delta 累计作为补充）
    final_answer = end_answer or answer_text
    answer_preview = final_answer[:500]
    fallback_markers = ("没有找到足够可靠", "无法从知识库", "未命中")
    is_fallback = any(marker in final_answer for marker in fallback_markers)
    has_answer = len(final_answer.strip()) > 20
    record(
        "answer",
        has_answer,
        {
            "chars": len(final_answer),
            "delta_chars": len(answer_text),
            "is_fallback": is_fallback,
            "preview": answer_preview,
        },
    )

    report["evidence_count"] = evidence_count

    # 6. 分环节耗时统计（基于 SSE 事件到达时间戳）
    def ts_of(event_name: str, occurrence: int = 1) -> float | None:
        seen = 0
        for ts, name in timeline:
            if name == event_name:
                seen += 1
                if seen == occurrence:
                    return ts
        return None

    def last_ts_of(event_name: str) -> float | None:
        result = None
        for ts, name in timeline:
            if name == event_name:
                result = ts
        return result

    t_start = ts_of("start") or 0.0
    t_understanding = ts_of("understanding")
    t_evidence = ts_of("evidence")
    t_first_delta = ts_of("delta")
    t_last_delta = last_ts_of("delta")
    t_end = ts_of("end")

    phases = {
        "login_seconds": login_seconds,
        "create_session_seconds": create_session_seconds,
        "understanding_seconds": round(t_understanding - t_start, 2) if t_understanding is not None else None,
        "retrieval_filtering_seconds": round(t_evidence - t_understanding, 2) if t_evidence is not None and t_understanding is not None else None,
        "generation_first_token_seconds": round(t_first_delta - t_evidence, 2) if t_first_delta is not None and t_evidence is not None else None,
        "delta_streaming_seconds": round(t_last_delta - t_first_delta, 2) if t_last_delta is not None and t_first_delta is not None else None,
        "post_processing_seconds": round(t_end - t_last_delta, 2) if t_end is not None and t_last_delta is not None else None,
        "sse_total_seconds": elapsed,
        "script_total_seconds": round(time.time() - t_script_start, 2),
        "delta_count": len([1 for _, name in timeline if name == "delta"]),
    }
    report["phase_breakdown"] = phases
    finish(overall_ok=True)
    print(f"Overall OK. Evidence chunks: {evidence_count}. Answer chars: {len(final_answer)}.")
    print("PHASE_BREAKDOWN:")
    for key, value in phases.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
