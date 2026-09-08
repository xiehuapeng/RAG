from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import dataclass
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

_NO_PROXIES = {"http": None, "https": None}


def _make_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.proxies.update(_NO_PROXIES)
    return session


# =========================
# 配置：均支持环境变量覆盖
# =========================
# 待裁判的报告文件（规则评分脚本产物），留空默认 <脚本目录>\reports\qa_test_report.json
REPORT_PATH = os.getenv("REPORT_PATH", r"")

OUTPUT_DIR = os.getenv("OUTPUT_DIR", r"")

# 裁判模型配置（OpenAI 兼容接口）
BASE_URL = os.getenv("JUDGE_BASE_URL", r"http://43.108.48.44/v1")
API_KEY = os.getenv("JUDGE_API_KEY", r"")
MODEL = os.getenv("JUDGE_MODEL", r"deepseek-v4-pro")
API_PATH = os.getenv("JUDGE_API_PATH", r"/chat/completions")

TIMEOUT_SECONDS = 120
MAX_RETRIES = 0
TEMPERATURE = 0.0
REQUEST_INTERVAL_SECONDS = 2.0

# 建议先设为 False，单条失败不中断整批任务
STOP_ON_ERROR = False

# 如果 REPORT_PATH 为空，会默认读取 <脚本目录>/reports/qa_test_report.json
DEFAULT_REPORT_DIRNAME = "reports"
DEFAULT_REPORT_FILENAME = "qa_test_report.json"

# 如果 OUTPUT_DIR 为空，会默认输出到 <report_dir>/judge_reports
DEFAULT_OUTPUT_DIRNAME = "judge_reports"


@dataclass
class JudgeConfig:
    report_path: Path
    output_dir: Path
    base_url: str
    api_key: str
    model: str
    api_path: str
    timeout_seconds: int
    max_retries: int
    temperature: float
    request_interval_seconds: float
    stop_on_error: bool


def build_config() -> JudgeConfig:
    if REPORT_PATH.strip():
        report_path = Path(REPORT_PATH).resolve()
    else:
        report_path = Path(__file__).resolve().parent / DEFAULT_REPORT_DIRNAME / DEFAULT_REPORT_FILENAME
    if OUTPUT_DIR.strip():
        output_dir = Path(OUTPUT_DIR).resolve()
    else:
        output_dir = report_path.parent / DEFAULT_OUTPUT_DIRNAME

    return JudgeConfig(
        report_path=report_path,
        output_dir=output_dir,
        base_url=BASE_URL.rstrip("/"),
        api_key=API_KEY.strip(),
        model=MODEL.strip(),
        api_path=API_PATH if API_PATH.startswith("/") else f"/{API_PATH}",
        timeout_seconds=max(int(TIMEOUT_SECONDS), 1),
        max_retries=max(int(MAX_RETRIES), 0),
        temperature=float(TEMPERATURE),
        request_interval_seconds=max(float(REQUEST_INTERVAL_SECONDS), 0.0),
        stop_on_error=bool(STOP_ON_ERROR),
    )


def load_report(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("report root must be a JSON object")
    results = data.get("results")
    if not isinstance(results, list):
        raise ValueError("report.results must be a list")
    return data


def build_system_prompt() -> str:
    return (
        "你是一个严格但贴近人工判断的业务问答评测裁判。"
        "你只能依据用户提供的 question、reference_answer、actual_answer、category、source_basis 做判断，"
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

        "请只输出合法 JSON，不要输出解释文字，不要输出 markdown。"
    )


def build_user_prompt(item: dict[str, Any]) -> str:
    payload = {
        "question": item.get("question", ""),
        "category": item.get("category", ""),
        "source_basis": item.get("source_basis", ""),
        "reference_answer": item.get("reference_answer", ""),
        "actual_answer": item.get("actual_answer", ""),
        "rule_threshold": item.get("threshold"),
        "existing_rule_passed": item.get("passed"),
        "existing_rule_score": {
            "must_have_score": item.get("must_have_score"),
            "nice_to_have_score": item.get("nice_to_have_score"),
            "weighted_score": item.get("weighted_score"),
            "expected_score": item.get("expected_score"),
            "optional_score": item.get("optional_score"),
            "total_score": item.get("total_score"),
        },
    }

    output_contract = {
        "judge_passed": True,
        "judge_score": 0.0,
        "factual_correctness": 0,
        "coverage": 0,
        "constraint_handling": 0,
        "actionability": 0,
        "clarity": 0,
        "factual_conflict": False,
        "major_missing_points": [],
        "major_errors": [],
        "brief_reason": "",
    }

    rubric = {
        "factual_correctness": "0-2：核心事实是否正确；只有核心结论或关键操作与参考答案冲突时才给低分",
        "coverage": "0-2：是否覆盖了主要信息点；不要因为措辞不同或有合理补充就判低",
        "constraint_handling": "0-2：是否说清关键限制、例外、前置条件；若漏掉会误导操作的限制，则降分",
        "actionability": "0-2：是否足够指导一线实际操作；只要核心可执行即可给中高分",
        "clarity": "0-2：是否清晰、直接、基本无歧义；啰嗦但不误导时不应给 0 分",
        "judge_score": "0.0-1.0，建议按总分/10换算；主体正确但略有冗余或补充不稳时，通常应在 0.6 以上",
        "judge_passed": "若核心结论正确、主要要点基本覆盖、无关键误导，则为 true；只有核心错误、关键遗漏、关键冲突、明显跑题时才为 false",
        "factual_conflict": "只有核心结论或关键操作指引与参考答案冲突时为 true；不能因为有额外补充信息就设为 true"
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
        "2. factual_correctness/coverage/constraint_handling/actionability/clarity 只能是 0/1/2。\n"
        "3. major_missing_points 和 major_errors 必须是字符串数组。\n"
        "4. brief_reason 控制在 1-3 句，简洁明确。\n"
        "5. 只有在核心结论错误、关键限制遗漏、关键操作冲突、明显跑题时，judge_passed 才应为 false。\n\n"
        f"输出 JSON 示例:\n{json.dumps(output_contract, ensure_ascii=False, indent=2)}"
    )


def extract_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty model output")
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("model output JSON is not an object")
        return data
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    candidate = text[start : end + 1]
    data = json.loads(candidate)
    if not isinstance(data, dict):
        raise ValueError("model output JSON is not an object")
    return data


def validate_judge_output(data: dict[str, Any]) -> dict[str, Any]:
    required = {
        "judge_passed",
        "judge_score",
        "factual_correctness",
        "coverage",
        "constraint_handling",
        "actionability",
        "clarity",
        "factual_conflict",
        "major_missing_points",
        "major_errors",
        "brief_reason",
    }
    missing = sorted(required - set(data))
    if missing:
        raise ValueError(f"judge output missing fields: {', '.join(missing)}")

    result = {
        "judge_passed": bool(data["judge_passed"]),
        "judge_score": float(data["judge_score"]),
        "factual_correctness": int(data["factual_correctness"]),
        "coverage": int(data["coverage"]),
        "constraint_handling": int(data["constraint_handling"]),
        "actionability": int(data["actionability"]),
        "clarity": int(data["clarity"]),
        "factual_conflict": bool(data["factual_conflict"]),
        "major_missing_points": [str(x).strip() for x in (data["major_missing_points"] or []) if str(x).strip()],
        "major_errors": [str(x).strip() for x in (data["major_errors"] or []) if str(x).strip()],
        "brief_reason": str(data["brief_reason"]).strip(),
    }

    for key in ("factual_correctness", "coverage", "constraint_handling", "actionability", "clarity"):
        if result[key] not in (0, 1, 2):
            raise ValueError(f"{key} must be 0/1/2")
    if not 0 <= result["judge_score"] <= 1:
        raise ValueError("judge_score must be between 0 and 1")
    return result


def call_judge_api(session: requests.Session, config: JudgeConfig, item: dict[str, Any]) -> tuple[dict[str, Any], str]:
    url = f"{config.base_url}{config.api_path}"
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "Connection": "close",
    }
    payload = {
        "model": config.model,
        "temperature": config.temperature,
        "messages": [
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": build_user_prompt(item)},
        ],
    }

    response = session.post(url, headers=headers, json=payload, timeout=config.timeout_seconds)
    response.raise_for_status()
    raw_text = response.content.decode("utf-8", errors="replace")
    body = json.loads(raw_text)

    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("judge API response missing choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("judge API response missing message")
    content = message.get("content")

    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(str(block.get("text") or ""))
        content_text = "\n".join(text_parts).strip()
    else:
        content_text = str(content or "").strip()

    parsed = extract_json_object(content_text)
    return validate_judge_output(parsed), content_text


def judge_one(item: dict[str, Any], config: JudgeConfig) -> tuple[dict[str, Any], bool]:
    started_at = time.perf_counter()
    last_error = ""
    last_raw_reply = ""

    for attempt in range(config.max_retries + 1):
        try:
            with _make_session() as session:
                judged, raw_reply = call_judge_api(session, config, item)
            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
            return ({
                **item,
                **judged,
                "judge_raw_reply": raw_reply,
                "judge_error": "",
                "judge_elapsed_ms": elapsed_ms,
            }, True)
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt >= config.max_retries:
                break
            time.sleep(min(2 ** attempt, 4))

    elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
    return ({
        **item,
        "judge_passed": False,
        "judge_score": 0.0,
        "factual_correctness": 0,
        "coverage": 0,
        "constraint_handling": 0,
        "actionability": 0,
        "clarity": 0,
        "factual_conflict": False,
        "major_missing_points": [],
        "major_errors": [],
        "brief_reason": "",
        "judge_raw_reply": last_raw_reply,
        "judge_error": last_error,
        "judge_elapsed_ms": elapsed_ms,
    }, False)


def build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    judged_passed = sum(1 for item in results if item.get("judge_passed"))
    judge_errors = sum(1 for item in results if item.get("judge_error"))
    factual_conflicts = sum(1 for item in results if item.get("factual_conflict"))
    avg_judge_score = sum(float(item.get("judge_score", 0.0)) for item in results) / total if total else 0.0
    return {
        "total": total,
        "judge_passed": judged_passed,
        "judge_failed": total - judged_passed,
        "judge_errors": judge_errors,
        "factual_conflicts": factual_conflicts,
        "judge_pass_rate": round(judged_passed / total, 4) if total else 0.0,
        "avg_judge_score": round(avg_judge_score, 4),
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
        "actual_answer",
        "passed",
        "must_have_score",
        "weighted_score",
        "expected_score",
        "total_score",
        "judge_passed",
        "judge_score",
        "factual_correctness",
        "coverage",
        "constraint_handling",
        "actionability",
        "clarity",
        "factual_conflict",
        "major_missing_points",
        "major_errors",
        "brief_reason",
        "judge_raw_reply",
        "error",
        "judge_error",
        "elapsed_ms",
        "judge_elapsed_ms",
    ]
    with output_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for item in results:
            row = dict(item)
            row["major_missing_points"] = " | ".join(row.get("major_missing_points") or [])
            row["major_errors"] = " | ".join(row.get("major_errors") or [])
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def save_reports(config: JudgeConfig, source_report: dict[str, Any], results: list[dict[str, Any]]) -> None:
    judged_summary = build_summary(results)
    merged_report = {
        "metadata": {
            "source_report_path": str(config.report_path),
            "judge_base_url": config.base_url,
            "judge_model": config.model,
            "judge_api_path": config.api_path,
            "timeout_seconds": config.timeout_seconds,
            "max_retries": config.max_retries,
            "temperature": config.temperature,
            "request_interval_seconds": config.request_interval_seconds,
            "stop_on_error": config.stop_on_error,
        },
        "source_summary": source_report.get("summary", {}),
        "judge_summary": judged_summary,
        "results": results,
    }

    json_path = config.output_dir / "qa_test_report_judged.json"
    csv_path = config.output_dir / "qa_test_report_judged.csv"
    write_json_report(merged_report, json_path)
    write_csv_report(results, csv_path)


def print_case_result(index: int, total: int, item: dict[str, Any], success: bool) -> None:
    case_id = str(item.get("id") or f"case_{index}")
    case_name = str(item.get("name") or "").strip()
    if success:
        print(
            f"[{index}/{total}] SUCCESS {case_id} | {case_name} | "
            f"judge_passed={item.get('judge_passed')} judge_score={item.get('judge_score')} "
            f"elapsed_ms={item.get('judge_elapsed_ms')}",
            flush=True,
        )
    else:
        print(
            f"[{index}/{total}] ERROR   {case_id} | {case_name} | "
            f"elapsed_ms={item.get('judge_elapsed_ms')} | {item.get('judge_error')}",
            flush=True,
        )


def main() -> int:
    config = build_config()
    if not config.report_path.exists():
        raise FileNotFoundError(f"report file not found: {config.report_path}")
    if not config.base_url:
        raise ValueError("BASE_URL is empty")
    if not config.api_key:
        raise ValueError("API_KEY is empty")
    if not config.model:
        raise ValueError("MODEL is empty")

    source_report = load_report(config.report_path)
    items = source_report["results"]
    config.output_dir.mkdir(parents=True, exist_ok=True)

    judged_results: list[dict[str, Any]] = []
    total = len(items)
    print(f"Starting LLM judge: total_cases={total}", flush=True)

    for index, item in enumerate(items, start=1):
        case_id = str(item.get("id") or f"case_{index}")
        case_name = str(item.get("name") or "").strip()
        print(f"[{index}/{total}] Running {case_id} | {case_name}", flush=True)

        judged_item, success = judge_one(item, config)
        judged_results.append(judged_item)
        print_case_result(index, total, judged_item, success)

        save_reports(config, source_report, judged_results)

        if not success and config.stop_on_error:
            print(
                f"Stopped early because STOP_ON_ERROR=True and case {case_id} failed.",
                flush=True,
            )
            break

        if config.request_interval_seconds > 0 and index < total:
            time.sleep(config.request_interval_seconds)

    judged_summary = build_summary(judged_results)
    print("\nLLM judge summary")
    print("-" * 88)
    print(
        f"processed={judged_summary['total']} judge_passed={judged_summary['judge_passed']} "
        f"judge_failed={judged_summary['judge_failed']} judge_errors={judged_summary['judge_errors']} "
        f"factual_conflicts={judged_summary['factual_conflicts']} "
        f"judge_pass_rate={judged_summary['judge_pass_rate']:.2%} "
        f"avg_judge_score={judged_summary['avg_judge_score']:.2%}"
    )
    print(f"reports: {config.output_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
