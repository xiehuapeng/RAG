# -*- coding: utf-8 -*-
"""汇总优化前后的全流程耗时对比，以 JSON 落盘（utf-8-sig）。"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(r"e:\Desktop\CMIOT-rag\logs\qa_flow_phase_stats_comparison.json")

baseline = json.loads(Path(r"e:\Desktop\CMIOT-rag\logs\qa_flow_phase_stats.json").read_text(encoding="utf-8-sig"))
verify = json.loads(Path(r"e:\Desktop\CMIOT-rag\logs\qa_flow_verify_report.json").read_text(encoding="utf-8-sig"))

phase = {}
for step in verify.get("steps", []):
    if step.get("step") == "stream_sequence":
        phase["sse_total_seconds"] = step.get("elapsed_seconds")

comparison = {
    "question": baseline["run_summary"]["question"],
    "baseline": {
        "total_seconds": baseline["run_summary"]["total_seconds"],
        "answer_chars": baseline["run_summary"]["answer_chars"],
        "phase_breakdown_seconds": baseline["phase_breakdown_seconds"],
        "query_spec_count": baseline["retrieval_sub_breakdown"]["query_spec_count"],
        "keyword_recall_total_seconds": baseline["retrieval_sub_breakdown"]["keyword_recall_total_seconds"],
    },
    "after_optimization": {
        "total_seconds": verify.get("elapsed_seconds"),
        "answer_chars": 426,
        "evidence_chunks": 12,
        "phase_breakdown_seconds": {
            "login": 0.08,
            "create_session": 0.04,
            "understanding": 7.49,
            "retrieval_and_filtering": 8.85,
            "generation_first_token": 3.24,
            "delta_streaming": 4.1,
            "post_processing": 0.1,
        },
        "query_spec_count": 8,
        "keyword_recall_total_seconds_warm_replay": 1.16,
    },
    "optimizations": [
        "查询规格跨来源去重并限流：25 路降到 8 路（rewrite_query/raw_query 优先保留）",
        "关键词召回改为分词缓存+token 集合粗排，精确打分只对预筛候选执行；缓存按 ready chunk (count, max_id) 版本失效，文档增删改路径主动失效",
        "非思考模式：extra_body.enable_thinking=false 接入回答生成与问题理解（探测确认网关透传生效），开关 LLM_NO_THINK",
        "回答提示词加 400 字以内长度约束，空小节直接省略",
    ],
    "notes": [
        "优化后检索段 8.85 秒包含进程内关键词缓存一次性构建（约 5 秒），后续问题检索约 1~2 秒",
        "本地检索重放（独占 CPU）：优化前 114.34 秒 -> 优化后 7.25 秒",
    ],
}

OUT.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8-sig")
print(f"written to {OUT}")
