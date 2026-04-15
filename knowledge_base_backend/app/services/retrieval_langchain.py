from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import RETRIEVE_TOP_K
from app.models import Chunk, RetrievalLog
from app.schemas import QueryUnderstandingResult
from app.services.vector_store import query_chunks


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+")
VECTOR_CANDIDATE_MULTIPLIER = 3
KEYWORD_CANDIDATE_MULTIPLIER = 4
RELEVANCE_SCORE_THRESHOLD = 0.50
RELEVANCE_KEYWORD_FALLBACK_THRESHOLD = 0.20
RELEVANCE_VECTOR_FALLBACK_THRESHOLD = 0.55
LOW_VALUE_COVERAGE_TERMS = {"开户", "批量开户", "设置", "选择", "方法", "怎么", "如何"}


def _to_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _unique_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _load_chunk_metadata(chunk: Chunk) -> dict:
    if not chunk.metadata_json:
        return {}
    try:
        return json.loads(chunk.metadata_json)
    except json.JSONDecodeError:
        return {}


def _distance_to_score(distance: float | int | None) -> float:
    """把 LangChain Chroma 返回的距离值转换成越大越相关的得分。"""

    if distance is None:
        return 0.0
    score = 1 - float(distance)
    return round(max(0.0, min(1.0, score)), 4)


def tokenize(text: str) -> list[str]:
    """轻量分词。

    这里仍然保留项目原本的关键字召回逻辑，不把所有工作都交给向量检索。
    原因是很多内部文档问答在标题词、流程编号、设备型号上对关键词命中很敏感，
    混合检索比纯向量检索更稳。
    """

    tokens: list[str] = []
    for token in TOKEN_PATTERN.findall(text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            tokens.extend(list(token))
            if len(token) > 1:
                tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
        else:
            tokens.append(token)
    return tokens


def _cosine_similarity(query_tokens: list[str], text_tokens: list[str]) -> float:
    if not query_tokens or not text_tokens:
        return 0.0
    query_counts = Counter(query_tokens)
    text_counts = Counter(text_tokens)
    dot = sum(query_counts[token] * text_counts[token] for token in query_counts)
    norm_q = math.sqrt(sum(value * value for value in query_counts.values()))
    norm_t = math.sqrt(sum(value * value for value in text_counts.values()))
    if not norm_q or not norm_t:
        return 0.0
    return dot / (norm_q * norm_t)


def _keyword_score(query: str, query_tokens: list[str], chunk: Chunk) -> tuple[float, dict[str, float | int]]:
    title = (chunk.document.title or "").lower()
    content = (chunk.content or "").lower()
    title_tokens = tokenize(title)
    content_tokens = tokenize(content)

    overlap_count = sum(1 for token in set(query_tokens) if token in content_tokens)
    title_overlap_count = sum(1 for token in set(query_tokens) if token in title_tokens)
    overlap_ratio = overlap_count / max(len(set(query_tokens)), 1)
    title_overlap_ratio = title_overlap_count / max(len(set(query_tokens)), 1)
    cosine = _cosine_similarity(query_tokens, content_tokens)
    phrase_hit = 1 if query.lower() in content else 0
    title_phrase_hit = 1 if query.lower() in title else 0

    score = (
        0.35 * overlap_ratio
        + 0.20 * min(overlap_count / 6, 1.0)
        + 0.20 * cosine
        + 0.15 * title_overlap_ratio
        + 0.05 * phrase_hit
        + 0.05 * title_phrase_hit
    )

    return round(min(score, 1.0), 4), {
        "keyword_overlap_count": overlap_count,
        "keyword_overlap_ratio": round(overlap_ratio, 4),
        "title_overlap_count": title_overlap_count,
        "title_overlap_ratio": round(title_overlap_ratio, 4),
        "keyword_cosine": round(cosine, 4),
        "phrase_hit": phrase_hit,
        "title_phrase_hit": title_phrase_hit,
    }


def _freshness_score(updated_at: datetime | None) -> float:
    if updated_at is None:
        return 0.0
    days = max((datetime.utcnow() - updated_at).days, 0)
    if days <= 7:
        return 0.03
    if days <= 30:
        return 0.02
    if days <= 90:
        return 0.01
    return 0.0


def _is_structure_only_content(content: str, metadata: dict) -> bool:
    normalized = " ".join(str(content or "").split())
    if not normalized:
        return True

    section_title = " ".join(str(metadata.get("section_title") or "").split())
    chapter_path = " ".join(str(metadata.get("chapter_path") or "").split())
    if normalized in {section_title, chapter_path}:
        return True
    if chapter_path and normalized == chapter_path.replace(" / ", " "):
        return True
    return False


def _understanding_term_features(
    term: str,
    chunk: Chunk,
    *,
    content_override: str | None = None,
) -> dict[str, float | int]:
    normalized_term = (term or "").strip().lower()
    if not normalized_term:
        return {
            "term_overlap_count": 0,
            "term_overlap_ratio": 0.0,
            "term_title_overlap_count": 0,
            "term_title_overlap_ratio": 0.0,
        }

    title = (chunk.document.title or "").lower()
    content = (content_override or chunk.content or "").lower()
    title_tokens = tokenize(title)
    content_tokens = tokenize(content)
    term_tokens = tokenize(normalized_term)
    if not term_tokens:
        term_tokens = [normalized_term]

    overlap_count = sum(1 for token in set(term_tokens) if token in content_tokens)
    title_overlap_count = sum(1 for token in set(term_tokens) if token in title_tokens)
    overlap_ratio = overlap_count / max(len(set(term_tokens)), 1)
    title_overlap_ratio = title_overlap_count / max(len(set(term_tokens)), 1)
    return {
        "term_overlap_count": overlap_count,
        "term_overlap_ratio": round(overlap_ratio, 4),
        "term_title_overlap_count": title_overlap_count,
        "term_title_overlap_ratio": round(title_overlap_ratio, 4),
    }


def _keyword_recall(db: Session, query: str, query_tokens: list[str], top_k: int) -> list[dict]:
    rows = (
        db.query(Chunk)
        .join(Chunk.document)
        .filter(Chunk.document.has(status="ready"))
        .all()
    )

    candidates: list[dict] = []
    for chunk in rows:
        score, features = _keyword_score(query, query_tokens, chunk)
        if score <= 0:
            continue
        candidates.append(
            {
                "chunk": chunk,
                "keyword_score": score,
                "vector_score": 0.0,
                "distance": None,
                "channels": {"keyword"},
                "features": features,
                "content": chunk.content,
            }
        )

    candidates.sort(
        key=lambda item: (
            item["keyword_score"],
            item["features"]["title_overlap_count"],
            item["features"]["keyword_overlap_count"],
        ),
        reverse=True,
    )
    return candidates[: top_k * KEYWORD_CANDIDATE_MULTIPLIER]


def _vector_recall(db: Session, query: str, top_k: int) -> list[dict]:
    """LangChain 向量召回。

    `query_chunks` 现在返回 `[(langchain_document, distance), ...]`。
    这里仍然回表查询数据库，是为了继续以数据库记录作为最终权威来源，
    防止向量库里残留旧数据或状态不一致。
    """

    search_results = query_chunks(query=query, top_k=top_k * VECTOR_CANDIDATE_MULTIPLIER)
    if not search_results:
        return []

    candidates: list[dict] = []
    for langchain_doc, distance in search_results:
        metadata = langchain_doc.metadata or {}
        chunk_id = metadata.get("chunk_id")
        if chunk_id is None:
            continue

        chunk = (
            db.query(Chunk)
            .join(Chunk.document)
            .filter(Chunk.id == int(chunk_id), Chunk.document.has(status="ready"))
            .first()
        )
        if chunk is None:
            continue

        candidates.append(
            {
                "chunk": chunk,
                "keyword_score": 0.0,
                "vector_score": _distance_to_score(distance),
                "distance": round(float(distance), 4) if distance is not None else None,
                "channels": {"vector"},
                "features": {},
                "content": langchain_doc.page_content or chunk.content,
            }
        )
    return candidates


def _merge_candidates(keyword_candidates: list[dict], vector_candidates: list[dict]) -> list[dict]:
    merged: dict[int, dict] = {}

    for candidate in keyword_candidates + vector_candidates:
        chunk = candidate["chunk"]
        current = merged.get(chunk.id)
        if current is None:
            merged[chunk.id] = candidate
            continue

        current["channels"].update(candidate["channels"])
        current["keyword_score"] = max(current["keyword_score"], candidate["keyword_score"])
        current["vector_score"] = max(current["vector_score"], candidate["vector_score"])
        if current["distance"] is None or (
            candidate["distance"] is not None and candidate["distance"] < current["distance"]
        ):
            current["distance"] = candidate["distance"]
        if candidate["content"]:
            current["content"] = candidate["content"]
        current["features"].update(candidate["features"])

    return list(merged.values())


def _rerank_candidates(candidates: list[dict], understanding_terms: list[str] | None = None) -> list[dict]:
    reranked: list[dict] = []
    normalized_terms = _unique_keep_order(understanding_terms or [])

    for candidate in candidates:
        chunk = candidate["chunk"]
        metadata = _load_chunk_metadata(chunk)
        content = candidate["content"] or chunk.content
        is_structure_only = _is_structure_only_content(content, metadata)
        features = candidate["features"]
        keyword_score = candidate["keyword_score"]
        vector_score = candidate["vector_score"]
        title_hit_bonus = 0.08 if features.get("title_overlap_count", 0) else 0.0
        multi_channel_bonus = 0.12 if len(candidate["channels"]) >= 2 else 0.0
        freshness_bonus = _freshness_score(chunk.document.updated_at)
        understanding_bonus = 0.0

        if normalized_terms:
            max_term_overlap = 0.0
            max_term_title_overlap = 0.0
            for term in normalized_terms:
                term_features = _understanding_term_features(term, chunk, content_override=candidate.get("content"))
                max_term_overlap = max(max_term_overlap, float(term_features["term_overlap_ratio"]))
                max_term_title_overlap = max(max_term_title_overlap, float(term_features["term_title_overlap_ratio"]))
            understanding_bonus = min(0.12, 0.08 * max_term_overlap + 0.04 * max_term_title_overlap)

        final_score = (
            0.38 * keyword_score
            + 0.42 * vector_score
            + multi_channel_bonus
            + title_hit_bonus
            + freshness_bonus
            + understanding_bonus
        )

        reranked.append(
            {
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "document_title": chunk.document.title,
                "chunk_index": chunk.chunk_index,
                "section_title": metadata.get("section_title"),
                "chapter_path": metadata.get("chapter_path"),
                "content": content,
                "score": round(min(final_score, 1.0), 4),
                "keyword_score": round(keyword_score, 4),
                "vector_score": round(vector_score, 4),
                "distance": candidate["distance"],
                "channels": sorted(candidate["channels"]),
                "title_hit": bool(features.get("title_overlap_count", 0)),
                "freshness_bonus": round(freshness_bonus, 4),
                "understanding_bonus": round(understanding_bonus, 4),
                "is_structure_only": is_structure_only,
                "chroma_id": chunk.chroma_id,
            }
        )

    reranked.sort(
        key=lambda item: (
            item["score"],
            len(item["channels"]),
            item["title_hit"],
            item["vector_score"],
            item["keyword_score"],
        ),
        reverse=True,
    )
    return reranked


def _passes_relevance_gate(candidate: dict) -> bool:
    if bool(candidate.get("is_structure_only")):
        return False
    if float(candidate.get("score") or 0.0) >= RELEVANCE_SCORE_THRESHOLD:
        return True
    if bool(candidate.get("title_hit")):
        return True
    if (
        float(candidate.get("keyword_score") or 0.0) >= RELEVANCE_KEYWORD_FALLBACK_THRESHOLD
        and float(candidate.get("vector_score") or 0.0) >= RELEVANCE_VECTOR_FALLBACK_THRESHOLD
    ):
        return True
    return False


def _candidate_text(candidate: dict) -> str:
    parts = [
        candidate.get("content") or "",
        candidate.get("section_title") or "",
        candidate.get("chapter_path") or "",
        candidate.get("document_title") or "",
    ]
    return "\n".join(str(part) for part in parts if part)


def _coverage_terms(terms: list[str]) -> list[str]:
    unique_terms = _unique_keep_order(terms)
    specific_terms = [term for term in unique_terms if term not in LOW_VALUE_COVERAGE_TERMS]
    fallback_terms = [term for term in unique_terms if term in LOW_VALUE_COVERAGE_TERMS]
    return specific_terms + fallback_terms[:1]


def _coverage_match_score(candidate: dict, term: str) -> float:
    content = str(candidate.get("content") or "")
    section_title = str(candidate.get("section_title") or "")
    chapter_path = str(candidate.get("chapter_path") or "")
    searchable = _candidate_text(candidate)
    if term not in searchable:
        return -1.0

    coverage_score = float(candidate.get("score") or 0.0)
    if term in content:
        coverage_score += 0.4
    if term in section_title or term in chapter_path:
        coverage_score += 0.35
    if "操作步骤" in section_title or "操作步骤" in chapter_path:
        coverage_score += 0.3
    elif "业务规则" in section_title or "业务规则" in chapter_path:
        coverage_score -= 0.25
    if term in content and "设置" in content:
        coverage_score += 0.2
    if term in content and ("选择" in content or "选为" in content):
        coverage_score += 0.15
    if term == "折扣" and ("是否打折" in content or "折扣率" in content or "折扣价格" in content):
        coverage_score += 0.35
    return coverage_score


def _select_top_results(
    reranked_candidates: list[dict],
    top_k: int,
    coverage_terms: list[str] | None = None,
) -> list[dict]:
    eligible_candidates = [candidate for candidate in reranked_candidates if _passes_relevance_gate(candidate)]
    if not coverage_terms:
        return eligible_candidates[:top_k]

    selected: list[dict] = []
    selected_ids: set[int] = set()

    for term in _coverage_terms(coverage_terms):
        best_candidate = None
        best_score = -1.0
        for candidate in eligible_candidates:
            chunk_id = int(candidate.get("chunk_id") or 0)
            if chunk_id in selected_ids:
                continue
            match_score = _coverage_match_score(candidate, term)
            if match_score > best_score:
                best_candidate = candidate
                best_score = match_score

        if best_candidate is not None and best_score >= 0:
            selected.append(best_candidate)
            selected_ids.add(int(best_candidate.get("chunk_id") or 0))
            if len(selected) >= top_k:
                return selected

    for candidate in eligible_candidates:
        chunk_id = int(candidate.get("chunk_id") or 0)
        if chunk_id in selected_ids:
            continue
        selected.append(candidate)
        if len(selected) >= top_k:
            break

    return selected


def retrieve(db: Session, session_id: int | None, query: str, top_k: int = RETRIEVE_TOP_K) -> list[dict]:
    query_tokens = tokenize(query)
    logger.info(
        "[qa.retrieve.request] session_id=%s top_k=%s query=%s query_tokens=%s",
        session_id,
        top_k,
        query,
        _to_json(query_tokens),
    )
    keyword_candidates = _keyword_recall(db, query, query_tokens, top_k)
    print(f"Keyword candidates: {keyword_candidates}")
    vector_candidates = _vector_recall(db, query, top_k)
    print(f"vector_candidates : {vector_candidates}")
    merged_candidates = _merge_candidates(keyword_candidates, vector_candidates)
    reranked_candidates = _rerank_candidates(merged_candidates)
    print(f"merged_candidates : {merged_candidates}, reranked_candidates : {reranked_candidates}")
    retrieved_snapshot = [
        {
            "chunk_id": candidate["chunk"].id,
            "document_id": candidate["chunk"].document_id,
            "document_title": candidate["chunk"].document.title,
            "chunk_index": candidate["chunk"].chunk_index,
            "keyword_score": round(candidate["keyword_score"], 4),
            "vector_score": round(candidate["vector_score"], 4),
            "channels": sorted(candidate["channels"]),
            "chroma_id": candidate["chunk"].chroma_id,
        }
        for candidate in merged_candidates
    ]

    filtered_candidates = [candidate for candidate in reranked_candidates if _passes_relevance_gate(candidate)]
    top_results = _select_top_results(reranked_candidates, top_k)
    logger.info(
        "[qa.retrieve.response] session_id=%s query=%s keyword_candidates=%s vector_candidates=%s merged_candidates=%s filtered_candidates=%s top_results=%s",
        session_id,
        query,
        len(keyword_candidates),
        len(vector_candidates),
        len(merged_candidates),
        len(filtered_candidates),
        _to_json(top_results),
    )

    db.add(
        RetrievalLog(
            session_id=session_id,
            query=query,
            retrieved_chunks=json.dumps(retrieved_snapshot, ensure_ascii=False),
            reranked_chunks=json.dumps(top_results, ensure_ascii=False),
        )
    )
    db.commit()
    return top_results


def retrieve_with_understanding(
    db: Session,
    session_id: int | None,
    understanding: QueryUnderstandingResult,
    top_k: int = RETRIEVE_TOP_K,
) -> list[dict]:
    queries = _unique_keep_order(
        [
            understanding.rewrite_query,
            understanding.raw_query,
            *understanding.keywords_hit,
            *understanding.entities,
        ]
    )
    if not queries:
        queries = [understanding.raw_query]

    merged_candidates: dict[int, dict] = {}
    retrieved_snapshot: list[dict] = []

    for query in queries:
        query_tokens = tokenize(query)
        keyword_candidates = _keyword_recall(db, query, query_tokens, top_k)
        vector_candidates = _vector_recall(db, query, top_k)
        per_query_candidates = _merge_candidates(keyword_candidates, vector_candidates)

        for candidate in per_query_candidates:
            chunk = candidate["chunk"]
            existing = merged_candidates.get(chunk.id)
            if existing is None:
                merged_candidates[chunk.id] = candidate
                existing = merged_candidates[chunk.id]
            else:
                existing["channels"].update(candidate["channels"])
                existing["keyword_score"] = max(existing["keyword_score"], candidate["keyword_score"])
                existing["vector_score"] = max(existing["vector_score"], candidate["vector_score"])
                if existing["distance"] is None or (
                    candidate["distance"] is not None and candidate["distance"] < existing["distance"]
                ):
                    existing["distance"] = candidate["distance"]
                if candidate["content"]:
                    existing["content"] = candidate["content"]
                existing["features"].update(candidate["features"])

            retrieved_snapshot.append(
                {
                    "query": query,
                    "chunk_id": chunk.id,
                    "document_id": chunk.document_id,
                    "document_title": chunk.document.title,
                    "chunk_index": chunk.chunk_index,
                    "keyword_score": round(candidate["keyword_score"], 4),
                    "vector_score": round(candidate["vector_score"], 4),
                    "channels": sorted(candidate["channels"]),
                    "chroma_id": chunk.chroma_id,
                }
            )

    understanding_terms = _unique_keep_order([*understanding.keywords_hit, *understanding.entities])
    reranked_candidates = _rerank_candidates(list(merged_candidates.values()), understanding_terms=understanding_terms)
    filtered_candidates = [candidate for candidate in reranked_candidates if _passes_relevance_gate(candidate)]
    top_results = _select_top_results(reranked_candidates, top_k, coverage_terms=understanding_terms)

    db.add(
        RetrievalLog(
            session_id=session_id,
            query=json.dumps(
                {
                    "raw_query": understanding.raw_query,
                    "rewrite_query": understanding.rewrite_query,
                    "keywords": understanding.keywords_hit,
                    "entities": understanding.entities,
                    "filters": understanding.filters,
                },
                ensure_ascii=False,
            ),
            retrieved_chunks=json.dumps(retrieved_snapshot, ensure_ascii=False),
            reranked_chunks=json.dumps(top_results, ensure_ascii=False),
        )
    )
    db.commit()
    return top_results
