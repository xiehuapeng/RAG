from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


# =========================
# 启动阶段：清除代理环境变量
# 系统级 HTTP_PROXY/HTTPS_PROXY 通常指向 Clash Verge 的 127.0.0.1:7897，
# 关闭代理软件后 requests 仍会读到这些变量，导致公网可达的 43.108.48.44
# 反而因连接不到本地代理而失败。
# =========================
def _strip_proxy_env() -> None:
    proxy_keys = (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    )
    for key in proxy_keys:
        os.environ.pop(key, None)


_strip_proxy_env()

# requests Session 禁用代理的统一配置
_NO_PROXIES = {"http": None, "https": None}


def _make_session() -> requests.Session:
    # 创建一个禁用系统代理的 requests.Session。
    # trust_env=False 让 requests 不再读取 HTTP_PROXY 等环境变量，
    # proxies=_NO_PROXIES 则显式告诉适配器对 http/https 都不走代理。
    session = requests.Session()
    session.trust_env = False
    session.proxies.update(_NO_PROXIES)
    return session


# =========================
# 在这里直接填写配置
# =========================

# 1) 测试用例文件：留空默认 <脚本目录>\qa_test_case_4.json，支持 CASES_PATH 环境变量覆盖
CASES_PATH = os.getenv("CASES_PATH", r"")

# 2) 输出目录，留空则默认 <脚本目录>\reports_llm_judge
OUTPUT_DIR = r""

# 3) 知识库后端配置：用于生成 actual_answer
KB_BASE_URL = r"http://127.0.0.1:8000"
KB_USERNAME = r"admin"
KB_PASSWORD = r"admin123"

# 4) 模型裁判配置：用于直接判断 actual_answer vs reference_answer
# 支持通过环境变量 JUDGE_BASE_URL / JUDGE_API_KEY / JUDGE_MODEL / JUDGE_API_PATH 覆盖
JUDGE_BASE_URL = os.getenv("JUDGE_BASE_URL", r"http://43.108.48.44/v1")
JUDGE_API_KEY = os.getenv("JUDGE_API_KEY", r"")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", r"deepseek-v4-pro")
JUDGE_API_PATH = os.getenv("JUDGE_API_PATH", r"/chat/completions")

# 5) 运行参数
KB_TIMEOUT_SECONDS = 120
JUDGE_TIMEOUT_SECONDS = 120
JUDGE_MAX_RETRIES = 2
JUDGE_TEMPERATURE = 0.0
REQUEST_INTERVAL_SECONDS = 2.0
REUSE_KB_SESSION = False
STOP_ON_JUDGE_ERROR = False

# 如果 CASES_PATH 为空，会默认读取 <script_dir>/qa_test_case_4.json
DEFAULT_CASES_FILENAME = "qa_test_case_4.json"

# 如果 OUTPUT_DIR 为空，会默认输出到 <script_dir>/reports_llm_judge
DEFAULT_OUTPUT_DIRNAME = "reports_llm_judge"

# 后端接口
LOGIN_PATH = "/api/auth/login"
CREATE_SESSION_PATH = "/api/chat/sessions/create"
SEND_MESSAGE_PATH_TEMPLATE = "/api/chat/sessions/{session_id}/messages"


@dataclass
class AppConfig:
    cases_path: Path
    output_dir: Path
    kb_base_url: str
    kb_username: str
    kb_password: str
    kb_timeout_seconds: int
    judge_base_url: str
    judge_api_key: str
    judge_model: str
    judge_api_path: str
    judge_timeout_seconds: int
    judge_max_retries: int
    judge_temperature: float
    request_interval_seconds: float
    reuse_kb_session: bool
    stop_on_judge_error: bool


class JudgeResponseError(RuntimeError):
    """Raised when the judge API replies but its content cannot be parsed."""

    def __init__(self, message: str, raw_reply: str = "") -> None:
        super().__init__(message)
        self.raw_reply = raw_reply


def build_config() -> AppConfig:
    script_dir = Path(__file__).resolve().parent
    if CASES_PATH.strip():
        cases_path = Path(CASES_PATH).resolve()
    else:
        cases_path = script_dir / DEFAULT_CASES_FILENAME
    if OUTPUT_DIR.strip():
        output_dir = Path(OUTPUT_DIR).resolve()
    else:
        output_dir = script_dir / DEFAULT_OUTPUT_DIRNAME

    return AppConfig(
        cases_path=cases_path,
        output_dir=output_dir,
        kb_base_url=KB_BASE_URL.rstrip("/"),
        kb_username=KB_USERNAME,
        kb_password=KB_PASSWORD,
        kb_timeout_seconds=max(int(KB_TIMEOUT_SECONDS), 1),
        judge_base_url=JUDGE_BASE_URL.rstrip("/"),
        judge_api_key=JUDGE_API_KEY.strip(),
        judge_model=JUDGE_MODEL.strip(),
        judge_api_path=JUDGE_API_PATH if JUDGE_API_PATH.startswith("/") else f"/{JUDGE_API_PATH}",
        judge_timeout_seconds=max(int(JUDGE_TIMEOUT_SECONDS), 1),
        judge_max_retries=max(int(JUDGE_MAX_RETRIES), 0),
        judge_temperature=float(JUDGE_TEMPERATURE),
        request_interval_seconds=max(float(REQUEST_INTERVAL_SECONDS), 0.0),
        reuse_kb_session=bool(REUSE_KB_SESSION),
        stop_on_judge_error=bool(STOP_ON_JUDGE_ERROR),
    )


def load_cases(cases_path: Path) -> list[dict[str, Any]]:
    with cases_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError("case file root must be a JSON array")

    required_fields = {
        "id",
        "name",
        "question",
        "reference_answer",
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


def kb_login(session: requests.Session, config: AppConfig) -> str:
    body = post_json(
        session,
        f"{config.kb_base_url}{LOGIN_PATH}",
        {"username": config.kb_username, "password": config.kb_password},
        timeout_seconds=config.kb_timeout_seconds,
    )
    data = ensure_success_envelope(body, action="login")
    token = str(data.get("token") or "").strip()
    if not token:
        raise RuntimeError("login failed: token is empty")
    return token


def create_chat_session(session: requests.Session, config: AppConfig, token: str, title: str) -> int:
    body = post_json(
        session,
        f"{config.kb_base_url}{CREATE_SESSION_PATH}",
        {"title": title},
        timeout_seconds=config.kb_timeout_seconds,
        headers={"x-session-token": token},
    )
    data = ensure_success_envelope(body, action="create session")
    session_id_raw = data.get("id")
    try:
        return int(session_id_raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"create session failed: invalid session id {session_id_raw!r}") from exc


def ask_question(session: requests.Session, config: AppConfig, token: str, session_id: int, question: str) -> str:
    path = SEND_MESSAGE_PATH_TEMPLATE.format(session_id=session_id)
    body = post_json(
        session,
        f"{config.kb_base_url}{path}",
        {"content": question},
        timeout_seconds=config.kb_timeout_seconds,
        headers={"x-session-token": token},
    )
    data = ensure_success_envelope(body, action="send message")
    answer = data.get("content")
    if answer is None:
        raise RuntimeError("send message failed: data.content is missing")
    return str(answer)


def build_system_prompt() -> str:
    return (
        "你是一个严格但贴近人工判断的业务问答评测裁判。"
        "你只能依据用户提供的 question、reference_answer、actual_answer、source_basis 做判断，"
        "不能引入外部知识，不能脑补手册外信息。"
        "你的目标不是检查 actual_answer 是否逐字贴近 reference_answer，"
        "而是判断 actual_answer 是否在业务上核心正确、主要要点充分、不会误导实际操作。"

        "请遵循以下裁判原则："
        "1. 优先判断核心结论是否正确。"
        "2. 如果答案与 reference_answer 的核心结论一致，即使措辞不同、顺序不同、表达更口语化，也应视为有效覆盖。"
        "3. 如果答案包含 reference_answer 之外的补充信息，只要这些补充信息不与核心结论冲突、不会误导操作，就不能仅因超出参考答案而判失败。"
        "4. 只有在以下情况才应判为失败："
        "   - 核心结论错误；"
        "   - 漏掉关键限制条件或关键前提，可能导致错误办理；"
        "   - 给出与 reference_answer 冲突的操作指引；"
        "   - 主要内容跑题，无法回答用户问题。"
        "5. 对于主体正确但有轻微补充不稳、轻微超纲、表述不够收敛的答案，应倾向于给中高分，而不是直接判失败。"
        "6. factual_conflict 只在核心结论冲突或关键操作指引冲突时标记为 true；"
        "   不能因为答案多说了一些未在参考答案出现的内容，就标记 factual_conflict=true。"
        "7. 如果答案核心正确、主要要点已覆盖、没有明显误导，即使不够精炼，也可以 judge_passed=true。"
        "8. 当答案处于可通过也可不通过的边界时，只要核心结论正确且不会误导操作，应优先判为通过。"
        "请只输出一个合法 JSON 对象，不要输出思考过程、<think>、解释文字或 markdown。"
    )


def build_user_prompt(item: dict[str, Any]) -> str:
    payload = {
        "question": item.get("question", ""),
        "source_basis": item.get("source_basis", ""),
        "reference_answer": item.get("reference_answer", ""),
        "actual_answer": item.get("actual_answer", ""),
    }

    output_contract = {
        "judge_passed": True,
        "judge_score": 0.0,
        "factual_conflict": False,
        "major_missing_points": [],
        "major_errors": [],
        "brief_reason": "",
    }

    rubric = {
        "judge_score": "0.0-1.0，主体正确但略有冗余或补充不稳时，通常应在 0.6 以上",
        "judge_passed": "若核心结论正确、主要要点基本覆盖、无关键误导，则为 true；只有核心错误、关键遗漏、关键冲突、明显跑题时才为 false",
        "factual_conflict": "只有核心结论或关键操作指引与参考答案冲突时为 true；不能因为有额外补充信息就设为 true",
        "major_missing_points": "只列会影响业务判断或操作的关键缺失点",
        "major_errors": "只列会导致业务误判或误操作的关键错误",
        "brief_reason": "用 1-3 句说明通过或失败的核心原因",
    }

    return (
        "请根据以下输入进行裁判，并严格按要求输出 JSON。\n\n"
        f"输入数据:\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        f"评分 rubric:\n{json.dumps(rubric, ensure_ascii=False, indent=2)}\n\n"
        "特别要求:\n"
        "1. 不要把答案比参考答案多说了一些内容直接视为错误。\n"
        "2. 只要这些补充内容不影响核心结论、不误导操作，就仍可判通过。\n"
        "3. 对于主体正确，但有轻微超纲、轻微啰嗦、轻微不够收敛的答案，应倾向于给中高分。\n"
        "4. major_errors 只写真正会导致业务误判或误操作的问题，不要把普通补充信息写进去。\n"
        "5. major_missing_points 只写缺失的关键点，不要把次要细节写进去。\n"
        "6. 如果答案的核心结论正确，但只是没有完全按参考答案原句表述，不应判失败。\n\n"
        "输出要求:\n"
        "1. 只输出一个 JSON 对象。\n"
        "2. 不要输出 markdown 代码块，不要输出 <think>，不要输出 JSON 之外的任何文字。\n"
        "3. major_missing_points 和 major_errors 必须是字符串数组。\n"
        "4. judge_score 必须是 0.0 到 1.0 之间的小数。\n"
        "5. brief_reason 控制在 1-3 句，简洁明确。\n"
        "6. 只有在核心结论错误、关键限制遗漏、关键操作冲突、明显跑题时，judge_passed 才应为 false。\n\n"
        f"输出 JSON 示例:\n{json.dumps(output_contract, ensure_ascii=False, indent=2)}"
    )


JUDGE_OUTPUT_REQUIRED_FIELDS = {
    "judge_passed",
    "judge_score",
    "factual_conflict",
    "major_missing_points",
    "major_errors",
    "brief_reason",
}


def strip_think_blocks(text: str) -> str:
    return re.sub(r"<think\b[^>]*>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)


def iter_json_candidates(text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()

    def add_candidate(candidate_text: str) -> None:
        candidate_text = candidate_text.strip()
        if not candidate_text:
            return
        try:
            data = json.loads(candidate_text)
            if isinstance(data, dict):
                candidates.append(data)
            return
        except json.JSONDecodeError:
            pass

        for index, char in enumerate(candidate_text):
            if char != "{":
                continue
            try:
                data, _ = decoder.raw_decode(candidate_text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                candidates.append(data)

    add_candidate(text)

    for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL):
        add_candidate(match.group(1))

    without_think = strip_think_blocks(text)
    if without_think != text:
        add_candidate(without_think)
        for match in re.finditer(r"```(?:json)?\s*(.*?)```", without_think, flags=re.IGNORECASE | re.DOTALL):
            add_candidate(match.group(1))

    return candidates


def extract_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty model output")

    candidates = iter_json_candidates(text)
    if not candidates:
        raise ValueError("no JSON object found in model output")

    for candidate in candidates:
        if JUDGE_OUTPUT_REQUIRED_FIELDS.issubset(candidate):
            return candidate

    candidate_keys = sorted({key for candidate in candidates for key in candidate})
    raise ValueError(f"no judge JSON object found; candidate keys={candidate_keys}")


def validate_judge_output(data: dict[str, Any]) -> dict[str, Any]:
    missing = sorted(JUDGE_OUTPUT_REQUIRED_FIELDS - set(data))
    if missing:
        raise ValueError(f"judge output missing fields: {', '.join(missing)}")

    result = {
        "judge_passed": bool(data["judge_passed"]),
        "judge_score": float(data["judge_score"]),
        "factual_conflict": bool(data["factual_conflict"]),
        "major_missing_points": [str(x).strip() for x in (data["major_missing_points"] or []) if str(x).strip()],
        "major_errors": [str(x).strip() for x in (data["major_errors"] or []) if str(x).strip()],
        "brief_reason": str(data["brief_reason"]).strip(),
    }

    if not 0 <= result["judge_score"] <= 1:
        raise ValueError("judge_score must be between 0 and 1")
    return result


def call_judge_api(config: AppConfig, item: dict[str, Any]) -> tuple[dict[str, Any], str]:
    url = f"{config.judge_base_url}{config.judge_api_path}"
    headers = {
        "Authorization": f"Bearer {config.judge_api_key}",
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "Connection": "close",
    }
    payload = {
        "model": config.judge_model,
        "temperature": config.judge_temperature,
        "messages": [
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": build_user_prompt(item)},
        ],
    }

    raw_text = ""
    try:
        with _make_session() as session:
            response = session.post(url, headers=headers, json=payload, timeout=config.judge_timeout_seconds)
            raw_text = response.content.decode("utf-8", errors="replace")
            response.raise_for_status()
            body = json.loads(raw_text)
    except Exception as exc:  # noqa: BLE001
        raise JudgeResponseError(f"judge API response failed: {exc}", raw_text) from exc

    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise JudgeResponseError("judge API response missing choices", raw_text)
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise JudgeResponseError("judge API response missing message", raw_text)
    content = message.get("content")

    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(str(block.get("text") or ""))
        content_text = "\n".join(text_parts).strip()
    else:
        content_text = str(content or "").strip()

    try:
        parsed = extract_json_object(content_text)
        return validate_judge_output(parsed), content_text
    except Exception as exc:  # noqa: BLE001
        raise JudgeResponseError(f"judge output parse failed: {exc}", content_text) from exc


def run_case(
    kb_session: requests.Session,
    config: AppConfig,
    token: str,
    case: dict[str, Any],
    *,
    shared_session_id: int | None = None,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    actual_answer = ""
    kb_error = ""
    chat_session_id: int | None = shared_session_id

    try:
        if chat_session_id is None:
            chat_session_id = create_chat_session(kb_session, config, token, f"QA Eval - {case['id']}")
        actual_answer = ask_question(kb_session, config, token, chat_session_id, str(case["question"]))
    except Exception as exc:  # noqa: BLE001
        kb_error = f"{type(exc).__name__}: {exc}"

    judge_passed = False
    judge_score = 0.0
    factual_conflict = False
    major_missing_points: list[str] = []
    major_errors: list[str] = []
    brief_reason = ""
    judge_raw_reply = ""
    judge_error = ""

    if not kb_error:
        for attempt in range(config.judge_max_retries + 1):
            try:
                judged, raw_reply = call_judge_api(
                    config,
                    {
                        "question": case["question"],
                        "source_basis": case["source_basis"],
                        "reference_answer": case["reference_answer"],
                        "actual_answer": actual_answer,
                    },
                )
                judge_passed = judged["judge_passed"]
                judge_score = judged["judge_score"]
                factual_conflict = judged["factual_conflict"]
                major_missing_points = judged["major_missing_points"]
                major_errors = judged["major_errors"]
                brief_reason = judged["brief_reason"]
                judge_raw_reply = raw_reply
                judge_error = ""
                break
            except JudgeResponseError as exc:
                if exc.raw_reply:
                    judge_raw_reply = exc.raw_reply
                judge_error = f"{type(exc).__name__}: {exc}"
                if attempt >= config.judge_max_retries:
                    break
                time.sleep(min(2 ** attempt, 4))
            except Exception as exc:  # noqa: BLE001
                judge_error = f"{type(exc).__name__}: {exc}"
                if attempt >= config.judge_max_retries:
                    break
                time.sleep(min(2 ** attempt, 4))

    elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)

    return {
        "id": case["id"],
        "name": case["name"],
        "question": case["question"],
        "source_basis": case["source_basis"],
        "reference_answer": case["reference_answer"],
        "chat_session_id": chat_session_id,
        "actual_answer": actual_answer,
        "kb_error": kb_error,
        "judge_passed": judge_passed,
        "judge_score": judge_score,
        "factual_conflict": factual_conflict,
        "major_missing_points": major_missing_points,
        "major_errors": major_errors,
        "brief_reason": brief_reason,
        "judge_raw_reply": judge_raw_reply,
        "judge_error": judge_error,
        "elapsed_ms": elapsed_ms,
    }


def build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    judge_passed = sum(1 for item in results if item.get("judge_passed"))
    judge_failed = total - judge_passed
    kb_errors = sum(1 for item in results if item.get("kb_error"))
    judge_errors = sum(1 for item in results if item.get("judge_error"))
    factual_conflicts = sum(1 for item in results if item.get("factual_conflict"))
    avg_judge_score = sum(float(item.get("judge_score", 0.0)) for item in results) / total if total else 0.0
    return {
        "total": total,
        "judge_passed": judge_passed,
        "judge_failed": judge_failed,
        "kb_errors": kb_errors,
        "judge_errors": judge_errors,
        "factual_conflicts": factual_conflicts,
        "judge_pass_rate": round(judge_passed / total, 4) if total else 0.0,
        "avg_judge_score": round(avg_judge_score, 4),
    }


def build_breakdown(results: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        key = str(item.get(field) or "UNKNOWN")
        grouped.setdefault(key, []).append(item)

    rows: list[dict[str, Any]] = []
    for name, items in sorted(grouped.items(), key=lambda pair: pair[0]):
        total = len(items)
        passed = sum(1 for item in items if item.get("judge_passed"))
        kb_errors = sum(1 for item in items if item.get("kb_error"))
        judge_errors = sum(1 for item in items if item.get("judge_error"))
        avg_judge_score = sum(float(item.get("judge_score", 0.0)) for item in items) / total if total else 0.0
        rows.append(
            {
                field: name,
                "total": total,
                "judge_passed": passed,
                "judge_failed": total - passed,
                "kb_errors": kb_errors,
                "judge_errors": judge_errors,
                "judge_pass_rate": round(passed / total, 4) if total else 0.0,
                "avg_judge_score": round(avg_judge_score, 4),
            }
        )
    return rows


def write_json_report(report: dict[str, Any], output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)


def write_csv_report(results: list[dict[str, Any]], output_path: Path) -> None:
    fieldnames = [
        "id",
        "name",
        "question",
        "source_basis",
        "reference_answer",
        "chat_session_id",
        "actual_answer",
        "judge_passed",
        "judge_score",
        "factual_conflict",
        "major_missing_points",
        "major_errors",
        "brief_reason",
        "judge_raw_reply",
        "kb_error",
        "judge_error",
        "elapsed_ms",
    ]
    with output_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for item in results:
            row = dict(item)
            row["major_missing_points"] = " | ".join(row.get("major_missing_points") or [])
            row["major_errors"] = " | ".join(row.get("major_errors") or [])
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def save_reports(config: AppConfig, results: list[dict[str, Any]]) -> None:
    summary = build_summary(results)
    report = {
        "summary": summary,
        "breakdown_by_source_basis": build_breakdown(results, "source_basis"),
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cases_path": str(config.cases_path),
            "kb_base_url": config.kb_base_url,
            "judge_base_url": config.judge_base_url,
            "judge_model": config.judge_model,
            "judge_api_path": config.judge_api_path,
            "reuse_kb_session": config.reuse_kb_session,
            "request_interval_seconds": config.request_interval_seconds,
            "judge_max_retries": config.judge_max_retries,
            "evaluation_mode": "backend_answer + llm_judge_only",
        },
        "results": results,
    }

    json_path = config.output_dir / "qa_test_report_llm_judged.json"
    csv_path = config.output_dir / "qa_test_report_llm_judged.csv"
    write_json_report(report, json_path)
    write_csv_report(results, csv_path)


def print_case_result(index: int, total: int, result: dict[str, Any]) -> None:
    case_id = str(result.get("id") or f"case_{index}")
    case_name = str(result.get("name") or "").strip()

    if result.get("kb_error"):
        print(
            f"[{index}/{total}] KB_ERROR {case_id} | {case_name} | "
            f"elapsed_ms={result.get('elapsed_ms')} | {result.get('kb_error')}",
            flush=True,
        )
        return

    if result.get("judge_error"):
        print(
            f"[{index}/{total}] JUDGE_ERROR {case_id} | {case_name} | "
            f"elapsed_ms={result.get('elapsed_ms')} | {result.get('judge_error')}",
            flush=True,
        )
        return

    print(
        f"[{index}/{total}] {'PASSED' if result.get('judge_passed') else 'FAILED'} {case_id} | {case_name} | "
        f"judge_score={result.get('judge_score')} elapsed_ms={result.get('elapsed_ms')}",
        flush=True,
    )


def main() -> int:
    config = build_config()
    if not config.cases_path.exists():
        raise FileNotFoundError(f"case file not found: {config.cases_path}")
    if not config.kb_base_url:
        raise ValueError("KB_BASE_URL is empty")
    if not config.kb_username:
        raise ValueError("KB_USERNAME is empty")
    if not config.kb_password:
        raise ValueError("KB_PASSWORD is empty")
    if not config.judge_base_url:
        raise ValueError("JUDGE_BASE_URL is empty")
    if not config.judge_api_key:
        raise ValueError("JUDGE_API_KEY is empty")
    if not config.judge_model:
        raise ValueError("JUDGE_MODEL is empty")

    cases = load_cases(config.cases_path)
    config.output_dir.mkdir(parents=True, exist_ok=True)

    with _make_session() as kb_session:
        try:
            token = kb_login(kb_session, config)
            shared_session_id: int | None = None
            if config.reuse_kb_session:
                shared_session_id = create_chat_session(kb_session, config, token, "QA Eval - shared")
        except Exception as exc:  # noqa: BLE001
            print(f"Startup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2

        total = len(cases)
        print(f"Starting backend QA + LLM judge: total_cases={total}", flush=True)

        results: list[dict[str, Any]] = []
        for index, case in enumerate(cases, start=1):
            case_id = str(case.get("id") or f"case_{index}")
            case_name = str(case.get("name") or "").strip()
            print(f"[{index}/{total}] Running {case_id} | {case_name}", flush=True)

            result = run_case(kb_session, config, token, case, shared_session_id=shared_session_id)
            results.append(result)
            print_case_result(index, total, result)
            save_reports(config, results)

            if result.get("judge_error") and config.stop_on_judge_error:
                print(
                    f"Stopped early because STOP_ON_JUDGE_ERROR=True and case {case_id} failed.",
                    flush=True,
                )
                break

            if config.request_interval_seconds > 0 and index < total:
                time.sleep(config.request_interval_seconds)

    summary = build_summary(results)
    print("\nLLM judged evaluation summary")
    print("-" * 96)
    print(
        f"total={summary['total']} judge_passed={summary['judge_passed']} judge_failed={summary['judge_failed']} "
        f"kb_errors={summary['kb_errors']} judge_errors={summary['judge_errors']} "
        f"factual_conflicts={summary['factual_conflicts']} "
        f"judge_pass_rate={summary['judge_pass_rate']:.2%} avg_judge_score={summary['avg_judge_score']:.2%}"
    )
    print(f"reports: {config.output_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
