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
from app.services.vector_store import query_chunks


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+")
VECTOR_CANDIDATE_MULTIPLIER = 3
KEYWORD_CANDIDATE_MULTIPLIER = 4
RELEVANCE_SCORE_THRESHOLD = 0.50
RELEVANCE_KEYWORD_FALLBACK_THRESHOLD = 0.20
RELEVANCE_VECTOR_FALLBACK_THRESHOLD = 0.55


def _to_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


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


def _rerank_candidates(candidates: list[dict]) -> list[dict]:
    reranked: list[dict] = []

    for candidate in candidates:
        chunk = candidate["chunk"]
        features = candidate["features"]
        keyword_score = candidate["keyword_score"]
        vector_score = candidate["vector_score"]
        title_hit_bonus = 0.08 if features.get("title_overlap_count", 0) else 0.0
        multi_channel_bonus = 0.12 if len(candidate["channels"]) >= 2 else 0.0
        freshness_bonus = _freshness_score(chunk.document.updated_at)

        final_score = (
            0.38 * keyword_score
            + 0.42 * vector_score
            + multi_channel_bonus
            + title_hit_bonus
            + freshness_bonus
        )

        reranked.append(
            {
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "document_title": chunk.document.title,
                "content": candidate["content"] or chunk.content,
                "score": round(min(final_score, 1.0), 4),
                "keyword_score": round(keyword_score, 4),
                "vector_score": round(vector_score, 4),
                "distance": candidate["distance"],
                "channels": sorted(candidate["channels"]),
                "title_hit": bool(features.get("title_overlap_count", 0)),
                "freshness_bonus": round(freshness_bonus, 4),
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
            "keyword_score": round(candidate["keyword_score"], 4),
            "vector_score": round(candidate["vector_score"], 4),
            "channels": sorted(candidate["channels"]),
            "chroma_id": candidate["chunk"].chroma_id,
        }
        for candidate in merged_candidates
    ]

    filtered_candidates = [candidate for candidate in reranked_candidates if _passes_relevance_gate(candidate)]
    top_results = filtered_candidates[:top_k]
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
