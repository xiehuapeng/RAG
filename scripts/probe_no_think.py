# -*- coding: utf-8 -*-
"""探测网关上游模型是否支持非思考模式参数。

对若干常见的"关闭思考"参数变体分别发起一次短流式请求，
统计 reasoning_content 是否消失、首个正文 token 的延迟。
结果写入 logs/no_think_probe.json（真源），stdout 只做摘要。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "knowledge_base_backend"))

from app.services import llm_client  # noqa: E402

OUT_PATH = PROJECT_ROOT / "logs" / "no_think_probe.json"

QUESTION = "请用一句话说明什么是挂账。"

VARIANTS: list[tuple[str, dict]] = [
    ("baseline", {}),
    ("enable_thinking_false", {"enable_thinking": False}),
    ("thinking_disabled", {"thinking": {"type": "disabled"}}),
    ("reasoning_effort_none", {"reasoning_effort": "none"}),
    ("deepseek_thinking_false", {"thinking": {"type": "disabled"}}),
    ("chat_template_no_think", {"chat_template_kwargs": {"enable_thinking": False}}),
]


def probe_variant(name: str, extra_body: dict) -> dict:
    started = time.time()
    first_reasoning_seconds = None
    first_content_seconds = None
    reasoning_chars = 0
    content_chars = 0
    error = None
    try:
        for chunk in llm_client.stream_chat_completion(
            [{"role": "user", "content": QUESTION}],
            max_output_tokens=1024,
            extra_body=extra_body,
        ):
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None) or ""
            content = delta.content or ""
            if reasoning:
                if first_reasoning_seconds is None:
                    first_reasoning_seconds = round(time.time() - started, 2)
                reasoning_chars += len(reasoning)
            if content:
                if first_content_seconds is None:
                    first_content_seconds = round(time.time() - started, 2)
                content_chars += len(content)
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"

    return {
        "variant": name,
        "extra_body": extra_body,
        "error": error,
        "first_reasoning_seconds": first_reasoning_seconds,
        "first_content_seconds": first_content_seconds,
        "reasoning_chars": reasoning_chars,
        "content_chars": content_chars,
        "total_seconds": round(time.time() - started, 2),
        "no_thinking": error is None and reasoning_chars == 0 and content_chars > 0,
    }


def main() -> None:
    results = []
    for name, extra_body in VARIANTS:
        print(f"probing {name} ...", flush=True)
        result = probe_variant(name, extra_body)
        results.append(result)
        print(
            f"  no_thinking={result['no_thinking']} "
            f"reasoning_chars={result['reasoning_chars']} "
            f"content_chars={result['content_chars']} "
            f"first_content={result['first_content_seconds']}s "
            f"total={result['total_seconds']}s error={result['error']}",
            flush=True,
        )

    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    winners = [item["variant"] for item in results if item["no_thinking"]]
    print(f"probe written to {OUT_PATH}")
    print(f"no_thinking_ok_variants: {winners}")


if __name__ == "__main__":
    main()
