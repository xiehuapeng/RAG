from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin123"
DEFAULT_TIMEOUT_SECONDS = 120

LOGIN_PATH = "/api/auth/login"
CREATE_SESSION_PATH = "/api/chat/sessions/create"
SEND_MESSAGE_PATH_TEMPLATE = "/api/chat/sessions/{session_id}/messages"

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CASES_PATH = SCRIPT_DIR / "qa_test_cases_3_first20.json"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "reports"


@dataclass
class QaEvalConfig:
    base_url: str
    username: str
    password: str
    timeout_seconds: int
    cases_path: Path
    output_dir: Path
    fail_on_error: bool
    create_session_per_case: bool
    strict_fatal: bool


def parse_args() -> QaEvalConfig:
    parser = argparse.ArgumentParser(description="Run reference-answer QA evaluation cases.")
    parser.add_argument("--cases", default=str(DEFAULT_CASES_PATH), help="Path to qa_test_cases.json.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for JSON/CSV reports.")
    parser.add_argument("--fail-on-error", action="store_true", help="Return non-zero when any case has runtime/API error.")
    parser.add_argument("--reuse-session", action="store_true", help="Reuse one chat session for all cases.")
    parser.add_argument(
        "--non-strict-fatal",
        action="store_true",
        help="Do not force fail when fatal mistakes are triggered; keep result in report only.",
    )
    args = parser.parse_args()

    timeout_raw = os.getenv("QA_EVAL_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)).strip()
    try:
        timeout_seconds = int(timeout_raw)
    except ValueError:
        timeout_seconds = DEFAULT_TIMEOUT_SECONDS

    return QaEvalConfig(
        base_url=os.getenv("QA_EVAL_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        username=os.getenv("QA_EVAL_USERNAME", DEFAULT_USERNAME),
        password=os.getenv("QA_EVAL_PASSWORD", DEFAULT_PASSWORD),
        timeout_seconds=max(timeout_seconds, 1),
        cases_path=Path(args.cases).resolve(),
        output_dir=Path(args.output_dir).resolve(),
        fail_on_error=bool(args.fail_on_error),
        create_session_per_case=not args.reuse_session,
        strict_fatal=not args.non_strict_fatal,
    )


def sanitize_items(items: list[Any]) -> list[str]:
    cleaned: list[str] = []
    for raw in items:
        item = str(raw or "").strip()
        if item:
            cleaned.append(item)
    return cleaned


def normalize_text(value: Any) -> str:
    text = str(value or "").casefold().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def load_cases(cases_path: Path) -> list[dict[str, Any]]:
    with cases_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError("case file root must be a JSON array")

    required_fields = {
        "id",
        "name",
        "question",
        "must_have_points",
        "nice_to_have_points",
        "fatal_mistakes",
        "reference_answer",
        "threshold",
        "category",
        "source_basis",
    }

    seen_ids: set[str] = set()
    for index, case in enumerate(data, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"case #{index} must be an object")

        missing = sorted(required_fields - set(case))
        if missing:
            raise ValueError(f"case #{index} missing fields: {', '.join(missing)}")

        case_id = str(case["id"]).strip()
        if not case_id:
            raise ValueError(f"case #{index} id must not be empty")
        if case_id in seen_ids:
            raise ValueError(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)

        if not str(case["question"]).strip():
            raise ValueError(f"case {case_id} question must not be empty")
        if not str(case["reference_answer"]).strip():
            raise ValueError(f"case {case_id} reference_answer must not be empty")

        for field in ("must_have_points", "nice_to_have_points", "fatal_mistakes"):
            if not isinstance(case[field], list):
                raise ValueError(f"case {case_id} {field} must be a list")

        must_have_points = sanitize_items(case["must_have_points"])
        if not must_have_points:
            raise ValueError(f"case {case_id} must_have_points must contain at least one non-empty item")

        try:
            threshold = float(case["threshold"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"case {case_id} threshold must be numeric") from exc
        if not 0 <= threshold <= 1:
            raise ValueError(f"case {case_id} threshold must be between 0 and 1")

    return data


def post_json(
    session: requests.Session,
    url: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: int,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)

    response = session.post(url, json=payload, headers=request_headers, timeout=timeout_seconds)
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError(f"response body is not an object: {url}")
    return body


def ensure_success_envelope(body: dict[str, Any], *, action: str) -> dict[str, Any]:
    if body.get("code") != 0:
        raise RuntimeError(f"{action} failed: code={body.get('code')} message={body.get('message')}")
    data = body.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"{action} failed: data is not an object")
    return data


def login(session: requests.Session, config: QaEvalConfig) -> str:
    body = post_json(
        session,
        f"{config.base_url}{LOGIN_PATH}",
        {"username": config.username, "password": config.password},
        timeout_seconds=config.timeout_seconds,
    )
    data = ensure_success_envelope(body, action="login")
    token = str(data.get("token") or "").strip()
    if not token:
        raise RuntimeError("login failed: token is empty")
    return token


def create_chat_session(session: requests.Session, config: QaEvalConfig, token: str, title: str) -> int:
    body = post_json(
        session,
        f"{config.base_url}{CREATE_SESSION_PATH}",
        {"title": title},
        timeout_seconds=config.timeout_seconds,
        headers={"x-session-token": token},
    )
    data = ensure_success_envelope(body, action="create session")
    session_id_raw = data.get("id")
    try:
        return int(session_id_raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"create session failed: invalid session id {session_id_raw!r}") from exc


def ask_question(session: requests.Session, config: QaEvalConfig, token: str, session_id: int, question: str) -> str:
    path = SEND_MESSAGE_PATH_TEMPLATE.format(session_id=session_id)
    body = post_json(
        session,
        f"{config.base_url}{path}",
        {"content": question},
        timeout_seconds=config.timeout_seconds,
        headers={"x-session-token": token},
    )
    data = ensure_success_envelope(body, action="send message")
    answer = data.get("content")
    if answer is None:
        raise RuntimeError("send message failed: data.content is missing")
    return str(answer)


def match_items(answer: str, items: list[Any]) -> tuple[list[str], list[str]]:
    normalized_answer = normalize_text(answer)
    matched: list[str] = []
    missing: list[str] = []
    for item in sanitize_items(items):
        if normalize_text(item) in normalized_answer:
            matched.append(item)
        else:
            missing.append(item)
    return matched, missing


def score_answer(answer: str, case: dict[str, Any], *, strict_fatal: bool) -> dict[str, Any]:
    must_have_points = sanitize_items(case.get("must_have_points") or [])
    nice_to_have_points = sanitize_items(case.get("nice_to_have_points") or [])
    fatal_mistakes = sanitize_items(case.get("fatal_mistakes") or [])

    matched_must, missing_must = match_items(answer, must_have_points)
    matched_nice, missing_nice = match_items(answer, nice_to_have_points)
    triggered_fatal, _ = match_items(answer, fatal_mistakes)

    must_total = len(must_have_points)
    nice_total = len(nice_to_have_points)

    must_have_score = len(matched_must) / must_total if must_total else 0.0
    nice_to_have_score = len(matched_nice) / nice_total if nice_total else 0.0
    if nice_total:
        weighted_score = 0.85 * must_have_score + 0.15 * nice_to_have_score
    else:
        weighted_score = must_have_score

    threshold = float(case.get("threshold", 1.0))
    passed = must_have_score >= threshold
    if strict_fatal and triggered_fatal:
        passed = False

    return {
        "matched_must_have_points": matched_must,
        "missing_must_have_points": missing_must,
        "matched_nice_to_have_points": matched_nice,
        "missing_nice_to_have_points": missing_nice,
        "triggered_fatal_mistakes": triggered_fatal,
        "must_have_score": round(must_have_score, 4),
        "nice_to_have_score": round(nice_to_have_score, 4),
        "weighted_score": round(weighted_score, 4),
        "threshold": threshold,
        "passed": passed,
    }


def run_case(
    session: requests.Session,
    config: QaEvalConfig,
    token: str,
    case: dict[str, Any],
    *,
    shared_session_id: int | None = None,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    answer = ""
    error = ""
    chat_session_id: int | None = shared_session_id

    try:
        if chat_session_id is None:
            chat_session_id = create_chat_session(session, config, token, f"QA Eval - {case['id']}")
        answer = ask_question(session, config, token, chat_session_id, str(case["question"]))
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    scoring = score_answer(answer, case, strict_fatal=config.strict_fatal)
    if error:
        scoring["passed"] = False

    elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
    return {
        "id": case["id"],
        "name": case["name"],
        "question": case["question"],
        "category": case["category"],
        "source_basis": case["source_basis"],
        "reference_answer": case["reference_answer"],
        "chat_session_id": chat_session_id,
        "actual_answer": answer,
        **scoring,
        "error": error,
        "elapsed_ms": elapsed_ms,
    }


def build_breakdown(results: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in results:
        grouped[str(item.get(field) or "UNKNOWN")].append(item)

    rows: list[dict[str, Any]] = []
    for name, items in sorted(grouped.items(), key=lambda pair: pair[0]):
        total = len(items)
        passed = sum(1 for item in items if item.get("passed"))
        errored = sum(1 for item in items if item.get("error"))
        avg_must_score = sum(float(item.get("must_have_score", 0.0)) for item in items) / total if total else 0.0
        avg_weighted_score = sum(float(item.get("weighted_score", 0.0)) for item in items) / total if total else 0.0
        rows.append(
            {
                field: name,
                "total": total,
                "passed": passed,
                "failed": total - passed,
                "errored": errored,
                "pass_rate": round(passed / total, 4) if total else 0.0,
                "avg_must_have_score": round(avg_must_score, 4),
                "avg_weighted_score": round(avg_weighted_score, 4),
            }
        )
    return rows


def build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for item in results if item.get("passed"))
    failed = total - passed
    errored = sum(1 for item in results if item.get("error"))
    fatal_triggered = sum(1 for item in results if item.get("triggered_fatal_mistakes"))
    pass_rate = passed / total if total else 0.0
    avg_must_score = sum(float(item.get("must_have_score", 0.0)) for item in results) / total if total else 0.0
    avg_weighted_score = sum(float(item.get("weighted_score", 0.0)) for item in results) / total if total else 0.0
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "errored": errored,
        "fatal_triggered": fatal_triggered,
        "pass_rate": round(pass_rate, 4),
        "avg_must_have_score": round(avg_must_score, 4),
        "avg_weighted_score": round(avg_weighted_score, 4),
    }


def write_json_report(report: dict[str, Any], output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)


def write_csv_report(results: list[dict[str, Any]], output_path: Path) -> None:
    fieldnames = [
        "id",
        "name",
        "question",
        "category",
        "source_basis",
        "reference_answer",
        "chat_session_id",
        "actual_answer",
        "matched_must_have_points",
        "missing_must_have_points",
        "matched_nice_to_have_points",
        "missing_nice_to_have_points",
        "triggered_fatal_mistakes",
        "must_have_score",
        "nice_to_have_score",
        "weighted_score",
        "threshold",
        "passed",
        "error",
        "elapsed_ms",
    ]
    with output_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for item in results:
            row = dict(item)
            for key in (
                "matched_must_have_points",
                "missing_must_have_points",
                "matched_nice_to_have_points",
                "missing_nice_to_have_points",
                "triggered_fatal_mistakes",
            ):
                row[key] = " | ".join(row.get(key) or [])
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def print_summary(summary: dict[str, Any], results: list[dict[str, Any]], output_dir: Path) -> None:
    print("\nQA evaluation summary")
    print("-" * 96)
    print(
        f"total={summary['total']} passed={summary['passed']} failed={summary['failed']} "
        f"errored={summary['errored']} fatal_triggered={summary['fatal_triggered']} "
        f"pass_rate={summary['pass_rate']:.2%} avg_must={summary['avg_must_have_score']:.2%} "
        f"avg_weighted={summary['avg_weighted_score']:.2%}"
    )
    print(f"reports: {output_dir}")

    failed_results = [item for item in results if not item.get("passed")]
    if not failed_results:
        return

    print("\nTop failed cases")
    print("-" * 96)
    for item in sorted(failed_results, key=lambda result: (result.get("must_have_score", 0.0), result["id"]))[:15]:
        missing = ", ".join(item.get("missing_must_have_points") or []) or "-"
        fatal = ", ".join(item.get("triggered_fatal_mistakes") or []) or "-"
        error = item.get("error") or "-"
        print(
            f"{item['id']} | {item['name']} | must={item['must_have_score']} "
            f"threshold={item['threshold']} missing={missing} fatal={fatal} error={error}"
        )


def main() -> int:
    config = parse_args()
    cases = load_cases(config.cases_path)
    config.output_dir.mkdir(parents=True, exist_ok=True)

    with requests.Session() as session:
        shared_session_id: int | None = None
        try:
            token = login(session, config)
            if not config.create_session_per_case:
                shared_session_id = create_chat_session(session, config, token, "QA Eval - shared")
        except Exception as exc:
            print(f"Startup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2

        total_cases = len(cases)
        print(f"Starting QA evaluation: total_cases={total_cases}")
        results = []
        for index, case in enumerate(cases, start=1):
            case_id = str(case.get("id") or f"case_{index}")
            case_name = str(case.get("name") or "").strip()
            print(f"[{index}/{total_cases}] Running {case_id} | {case_name}", flush=True)

            result = run_case(session, config, token, case, shared_session_id=shared_session_id)
            results.append(result)

            if result.get("error"):
                print(
                    f"[{index}/{total_cases}] ERROR   {case_id} | elapsed_ms={result.get('elapsed_ms')} | {result.get('error')}",
                    flush=True,
                )
            else:
                status = "PASSED" if result.get("passed") else "FAILED"
                print(
                    f"[{index}/{total_cases}] {status:<7} {case_id} | "
                    f"must_have_score={result.get('must_have_score')} "
                    f"weighted_score={result.get('weighted_score')} "
                    f"fatal={','.join(result.get('triggered_fatal_mistakes') or []) or '-'} "
                    f"elapsed_ms={result.get('elapsed_ms')}",
                    flush=True,
                )

    summary = build_summary(results)
    report = {
        "summary": summary,
        "breakdown_by_category": build_breakdown(results, "category"),
        "breakdown_by_source_basis": build_breakdown(results, "source_basis"),
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "base_url": config.base_url,
            "cases_path": str(config.cases_path),
            "strict_fatal": config.strict_fatal,
            "create_session_per_case": config.create_session_per_case,
            "scoring": (
                "pass requires must_have_score >= threshold; "
                "if nice_to_have exists then weighted_score = 0.85 * must_have_score + 0.15 * nice_to_have_score "
                "else weighted_score = must_have_score; "
                "strict_fatal=true means any triggered_fatal_mistakes forces fail"
            ),
        },
        "results": results,
    }

    json_path = config.output_dir / "qa_test_report.json"
    csv_path = config.output_dir / "qa_test_report.csv"
    write_json_report(report, json_path)
    write_csv_report(results, csv_path)
    print_summary(summary, results, config.output_dir)

    if config.fail_on_error and any(item.get("error") for item in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
