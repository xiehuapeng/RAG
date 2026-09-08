"""按真实检索日志还原查询规格，对检索链路各子环节分步计时。

不重复发起 LLM 调用，只重放检索本身：
关键词召回 / 向量召回 / 候选合并 / 规则重排 / 分桶筛选。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from app.database import db_context
from app.models import Chunk, RetrievalLog
from app.schemas import QueryUnderstandingResult
from app.services import retrieval_langchain as R
from app.services.langchain_runtime import get_embeddings

OUT_PATH = Path(__file__).resolve().parent.parent / "logs" / "retrieval_profile.json"


def main() -> None:
    with db_context() as db:
        log_row = (
            db.query(RetrievalLog)
            .order_by(RetrievalLog.id.desc())
            .first()
        )
        if log_row is None:
            print("no retrieval log found")
            return

        query_payload = json.loads(log_row.query)
        chunk_total = db.query(Chunk).count()

        understanding = QueryUnderstandingResult(
            route="kb_qa",
            raw_query=query_payload.get("raw_query", ""),
            rewrite_query=query_payload.get("rewrite_query", ""),
            keywords_hit=query_payload.get("keywords") or [],
            entities=query_payload.get("entities") or [],
            search_terms=query_payload.get("search_terms") or [],
            search_queries=query_payload.get("search_queries") or [],
        )
        specs = R._query_specs_from_understanding(understanding)
        recall_top_k = max(12 * 2, 16)

    print(f"chunk_total={chunk_total} query_spec_count={len(specs)}", flush=True)
    for source, query in specs:
        print(f"  spec: {source} = {query[:60]}", flush=True)

    # 预热 embedding 模型，避免把首次加载时间计入检索统计
    t0 = time.time()
    get_embeddings().embed_query("预热")
    warmup_seconds = round(time.time() - t0, 2)
    print(f"embedding_warmup_seconds={warmup_seconds}", flush=True)

    keyword_times: list[float] = []
    vector_times: list[float] = []
    merged_candidates: dict[int, dict] = {}

    with db_context() as db:
        t_retrieval_start = time.time()
        for source, query in specs:
            t0 = time.time()
            query_tokens = R.tokenize(query)
            keyword_candidates = R._keyword_recall(db, query, query_tokens, recall_top_k)
            keyword_times.append(round(time.time() - t0, 2))

            t0 = time.time()
            vector_candidates = R._vector_recall(db, query, recall_top_k)
            vector_times.append(round(time.time() - t0, 2))

            per_query = R._merge_candidates(keyword_candidates, vector_candidates)
            for rank, candidate in enumerate(per_query, start=1):
                R._tag_candidate_source(candidate, source=source, query=query, rank=rank)
                R._merge_sourced_candidate(merged_candidates, candidate)

        retrieval_elapsed = round(time.time() - t_retrieval_start, 2)

        understanding_terms = R._unique_keep_order(
            [
                *understanding.search_terms,
                *understanding.search_queries,
                *understanding.keywords_hit,
                *understanding.entities,
            ]
        )

        t0 = time.time()
        reranked = R._rerank_candidates(list(merged_candidates.values()), understanding_terms=understanding_terms)
        rerank_seconds = round(time.time() - t0, 2)

        t0 = time.time()
        top_results = R._select_bucketed_top_results(reranked, 12, coverage_terms=understanding_terms)
        select_seconds = round(time.time() - t0, 2)

    result = {
        "chunk_total": chunk_total,
        "query_spec_count": len(specs),
        "specs": [{"source": s, "query": q[:80]} for s, q in specs],
        "understanding_term_count": len(understanding_terms),
        "merged_candidate_count": len(merged_candidates),
        "keyword_recall_per_query_seconds": keyword_times,
        "keyword_recall_total_seconds": round(sum(keyword_times), 2),
        "vector_recall_per_query_seconds": vector_times,
        "vector_recall_total_seconds": round(sum(vector_times), 2),
        "recall_loop_total_seconds": retrieval_elapsed,
        "rerank_seconds": rerank_seconds,
        "select_seconds": select_seconds,
        "final_top_count": len(top_results),
        "embedding_warmup_seconds": warmup_seconds,
    }
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    print("RESULT:")
    for key, value in result.items():
        print(f"  {key}: {value}")
    print(f"profile written to {OUT_PATH}")


if __name__ == "__main__":
    main()
