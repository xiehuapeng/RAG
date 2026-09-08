# -*- coding: utf-8 -*-
"""选项1验证：实测 SSE 流式与非流式问答的用户可感知延迟差异。

从 _tmp_cases_0607.json 读取测试问题（避免终端中文编码问题），
分别走 /messages/stream（SSE）与 /messages（非流式），记录关键时间点：
流式关注首 delta（用户看到第一个字）与 end（收完），非流式只有 total。
两个问题交替"先流式/先非流式"顺序，削弱缓存冷热带来的偏差。
结果写入 reports_llm_judge/probe_sse_latency.json（utf-8-sig）。
"""
import json
import time
from pathlib import Path

import requests

BASE_URL = "http://127.0.0.1:8000"
HERE = Path(__file__).resolve().parent
CASES_PATH = HERE / "_tmp_cases_0607.json"
OUTPUT_PATH = HERE / "reports_llm_judge" / "probe_sse_latency.json"
USERNAME = "admin"
PASSWORD = "admin123"
TIMEOUT = 300.0
INTERVAL_SECONDS = 2.0


def make_requests_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


def login(session: requests.Session) -> str:
    resp = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        timeout=30,
    )
    resp.raise_for_status()
    return str(resp.json()["data"]["token"])


def create_chat_session(session: requests.Session, token: str, title: str) -> int:
    resp = session.post(
        f"{BASE_URL}/api/chat/sessions/create",
        json={"title": title},
        headers={"x-session-token": token},
        timeout=30,
    )
    resp.raise_for_status()
    return int(resp.json()["data"]["id"])


def measure_stream(session: requests.Session, token: str, session_id: int, question: str) -> dict:
    url = f"{BASE_URL}/api/chat/sessions/{session_id}/messages/stream"
    t0 = time.perf_counter()
    first_marks: dict[str, float] = {}
    delta_count = 0
    delta_chars = 0
    stop_reason = ""
    with session.post(
        url,
        json={"content": question},
        headers={"x-session-token": token, "Accept": "text/event-stream"},
        stream=True,
        timeout=TIMEOUT,
    ) as resp:
        resp.raise_for_status()
        event_name = ""
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                event_name = ""
                continue
            line = raw_line.strip()
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
                continue
            if not line.startswith("data:"):
                continue
            elapsed = time.perf_counter() - t0
            payload_raw = line.split(":", 1)[1].strip()
            try:
                payload = json.loads(payload_raw)
            except json.JSONDecodeError:
                payload = {}
            if event_name and event_name not in first_marks:
                first_marks[event_name] = round(elapsed, 3)
            if event_name == "delta":
                delta_count += 1
                delta_chars += len(str(payload.get("content") or ""))
            if event_name in {"end", "error"}:
                stop_reason = event_name
                break
    return {
        "first_marks": first_marks,
        "delta_count": delta_count,
        "delta_chars": delta_chars,
        "stop_reason": stop_reason,
        "first_event_s": first_marks.get("start"),
        "understanding_s": first_marks.get("understanding"),
        "evidence_s": first_marks.get("evidence"),
        "first_delta_s": first_marks.get("delta"),
        "end_s": first_marks.get("end") or first_marks.get("error"),
    }


def measure_non_stream(session: requests.Session, token: str, session_id: int, question: str) -> dict:
    url = f"{BASE_URL}/api/chat/sessions/{session_id}/messages"
    t0 = time.perf_counter()
    resp = session.post(
        url,
        json={"content": question},
        headers={"x-session-token": token},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    elapsed = time.perf_counter() - t0
    content = str(resp.json().get("data", {}).get("content") or "")
    return {"total_s": round(elapsed, 3), "chars": len(content)}


def main() -> None:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    session = make_requests_session()
    token = login(session)
    report = []
    for index, case in enumerate(cases):
        question = str(case.get("question") or "").strip()
        case_id = str(case.get("id") or f"case_{index}")
        if not question:
            continue
        stream_first = index % 2 == 0
        row: dict = {
            "case_id": case_id,
            "question": question,
            "order": "stream_first" if stream_first else "non_stream_first",
        }
        if stream_first:
            sid = create_chat_session(session, token, f"probe-stream-{case_id}")
            row["stream"] = measure_stream(session, token, sid, question)
            time.sleep(INTERVAL_SECONDS)
            sid = create_chat_session(session, token, f"probe-nonstream-{case_id}")
            row["non_stream"] = measure_non_stream(session, token, sid, question)
        else:
            sid = create_chat_session(session, token, f"probe-nonstream-{case_id}")
            row["non_stream"] = measure_non_stream(session, token, sid, question)
            time.sleep(INTERVAL_SECONDS)
            sid = create_chat_session(session, token, f"probe-stream-{case_id}")
            row["stream"] = measure_stream(session, token, sid, question)
        report.append(row)
        time.sleep(INTERVAL_SECONDS)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    print("=== SSE probe report ===")
    for row in report:
        stream = row.get("stream") or {}
        non_stream = row.get("non_stream") or {}
        print(
            f"[{row['case_id']}] stream: first_delta={stream.get('first_delta_s')}s "
            f"end={stream.get('end_s')}s deltas={stream.get('delta_count')} chars={stream.get('delta_chars')} | "
            f"non_stream: total={non_stream.get('total_s')}s chars={non_stream.get('chars')}"
        )
    print(f"saved: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
