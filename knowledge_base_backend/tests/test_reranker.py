from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import retrieval_langchain as retrieval


def candidate(chunk_id, rank, score=0.7, source="raw_query"):
    return dict(chunk_id=chunk_id, content="evidence", score=score, rank_score=score,
                query_sources=[source], source_ranks={source: rank}, source_queries=[],
                channels=["vector"], title_hit=False, vector_score=0.7, keyword_score=0.1)


def test_bucket_uses_final_rank_before_old_source_rank():
    old_first = candidate(1, 1, 0.8)
    improved = candidate(2, 2, 1.1)
    old_first["source_queries"] = ["evidence"]
    assert retrieval._select_bucketed_top_results([improved, old_first], 1) == [improved]


def test_source_quotas_and_dedup_are_preserved():
    raw = [candidate(i, i) for i in range(1, 4)]
    rewrite = candidate(4, 1, source="rewrite_query")
    expanded = candidate(5, 1, 0.99, source="search_query")
    result = retrieval._select_bucketed_top_results([expanded, *raw, rewrite], 4)
    assert {item["chunk_id"] for item in result} == {1, 2, 4, 5}


def test_zero_limit_and_structure_only_candidates_are_excluded():
    item = candidate(1, 1)
    assert retrieval._select_bucketed_top_results([item], 0) == []
    item["is_structure_only"] = True
    assert retrieval._select_bucketed_top_results([item], 1) == []


def test_model_bonus_can_change_bucket_winner(monkeypatch):
    monkeypatch.setattr(retrieval, "RERANKER_ENABLED", True)
    monkeypatch.setattr(retrieval, "RERANKER_CE_FLOOR", 0.7)
    monkeypatch.setattr(retrieval, "RERANKER_SCORE_WEIGHT", 0.25)
    monkeypatch.setattr(retrieval, "get_reranker", lambda: SimpleNamespace(predict=Mock(return_value=[0.1, 1.0])))
    ranked = retrieval._cross_encoder_rerank([candidate(1, 1, 0.72), candidate(2, 2)], "query")
    assert retrieval._select_bucketed_top_results(ranked, 1)[0]["chunk_id"] == 2
    assert next(item for item in ranked if item["chunk_id"] == 1)["rank_score"] == 0.72


def test_model_failure_preserves_heuristic_order(monkeypatch):
    monkeypatch.setattr(retrieval, "RERANKER_ENABLED", True)
    monkeypatch.setattr(retrieval, "get_reranker", Mock(side_effect=RuntimeError("offline")))
    items = [candidate(1, 1), candidate(2, 2)]
    assert retrieval._cross_encoder_rerank(items, "query") is items


def test_followup_rerank_uses_rewritten_query(monkeypatch):
    monkeypatch.setattr(retrieval, "_keyword_recall", lambda *args: [])
    monkeypatch.setattr(retrieval, "_vector_recall", lambda *args: [])
    rerank = Mock(return_value=[])
    monkeypatch.setattr(retrieval, "_cross_encoder_rerank", rerank)
    understanding = SimpleNamespace(raw_query="and online?", rewrite_query="account opening online",
                                    keywords_hit=[], entities=[], search_terms=[], search_queries=[], filters={})
    retrieval.retrieve_with_understanding(Mock(), None, understanding)
    rerank.assert_called_once_with([], "account opening online")


@pytest.mark.parametrize("vector_id,expected", [("current-id", 1), ("orphan-id", 0), (None, 0)])
def test_vector_recall_checks_unique_identity_before_accepting_reused_sql_id(monkeypatch, vector_id, expected):
    chunk = SimpleNamespace(id=1, chroma_id="current-id", content="current SQL evidence")
    vector = SimpleNamespace(metadata={"chunk_id": 1, "chroma_id": vector_id}, page_content="stale vector text")
    monkeypatch.setattr(retrieval, "query_chunks", lambda **kwargs: [(vector, 0.1)])
    db = Mock()
    db.query.return_value.join.return_value.filter.return_value.first.return_value = chunk
    result = retrieval._vector_recall(db, "query", 1)
    assert len(result) == expected
    if result:
        assert result[0]["content"] == "current SQL evidence"
