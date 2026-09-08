# -*- coding: utf-8 -*-
"""选项2验证：重排候选上限 32 vs 16 对最终选块结果的影响。

同一份 query understanding 结果分别喂给 RERANKER_MAX_CANDIDATES=32 与 16
两种配置（运行时 patch 模块级常量，不改任何配置文件），对比最终 top_k 选块：
- identical_selection：两次选中的 chunk 序列是否完全一致；
- only_in_32 / only_in_16：仅在某一配置下进入 top_k 的 chunk。
若 identical 为真，则缩到 16 不影响问答选料，可安全落地。
结果写入 reports_llm_judge/probe_rerank_candidates.json（utf-8-sig）。
"""
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.services.retrieval_langchain as rl  # noqa: E402
from app.config import QA_RETRIEVE_TOP_K  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.services.query_understanding import get_query_understanding_service  # noqa: E402

HERE = Path(__file__).resolve().parent
CASES_PATH = HERE / "_tmp_cases_0607.json"
OUTPUT_PATH = HERE / "reports_llm_judge" / "probe_rerank_candidates.json"


def snapshot(results: list[dict]) -> list[dict]:
    rows = []
    for position, candidate in enumerate(results, start=1):
        chunk = candidate.get("chunk")
        rows.append(
            {
                "position": position,
                "chunk_id": candidate.get("chunk_id") or (chunk.id if chunk else None),
                "document_id": candidate.get("document_id") or getattr(chunk, "document_id", None),
                "chunk_index": getattr(chunk, "chunk_index", None),
                "heuristic_score": candidate.get("heuristic_score"),
                "reranker_score": candidate.get("reranker_score"),
                "reranker_bonus": candidate.get("reranker_bonus"),
                "rank_score": round(float(candidate.get("rank_score") or 0.0), 4),
                "display_score": candidate.get("score"),
                "best_source": candidate.get("best_source"),
                "keyword_score": round(float(candidate.get("keyword_score") or 0.0), 4),
                "vector_score": round(float(candidate.get("vector_score") or 0.0), 4),
                "text_head": (getattr(chunk, "content", "") or "")[:60],
            }
        )
    return rows


def main() -> None:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    questions = [str(case.get("question") or "").strip() for case in cases]
    questions = [q for q in questions if q]

    understanding_service = get_query_understanding_service()
    db = SessionLocal()
    report = []
    try:
        for question in questions:
            understanding = understanding_service.understand(query=question, history=[])

            rl.RERANKER_MAX_CANDIDATES = 32
            results_32 = rl.retrieve_with_understanding(db, None, understanding, top_k=QA_RETRIEVE_TOP_K)
            rl.RERANKER_MAX_CANDIDATES = 16
            results_16 = rl.retrieve_with_understanding(db, None, understanding, top_k=QA_RETRIEVE_TOP_K)
            rl.RERANKER_MAX_CANDIDATES = 32

            snap_32 = snapshot(results_32)
            snap_16 = snapshot(results_16)
            ids_32 = [row["chunk_id"] for row in snap_32]
            ids_16 = [row["chunk_id"] for row in snap_16]
            only_in_32 = [cid for cid in ids_32 if cid not in set(ids_16)]
            only_in_16 = [cid for cid in ids_16 if cid not in set(ids_32)]
            identical = ids_32 == ids_16
            report.append(
                {
                    "question": question,
                    "rewrite_query": understanding.rewrite_query,
                    "raw_query": understanding.raw_query,
                    "top_k": QA_RETRIEVE_TOP_K,
                    "ids_32": ids_32,
                    "ids_16": ids_16,
                    "identical_selection": identical,
                    "only_in_32": only_in_32,
                    "only_in_16": only_in_16,
                    "results_32": snap_32,
                    "results_16": snap_16,
                }
            )
            head = question[:24]
            print(f"[probe] {head}... identical={identical} only_in_32={only_in_32} only_in_16={only_in_16}")
    finally:
        db.close()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    identical_all = all(row["identical_selection"] for row in report)
    print(f"=== rerank candidates probe: identical_selection(all)={identical_all} ===")
    print(f"saved: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
