from __future__ import annotations

# 用于将检索轨迹写入 JSON 日志。
import json
# 用于计算词袋余弦相似度。
import math
# 用于轻量分词。
import re
# 用于统计 token 频次。
from collections import Counter
# Iterable 仅用于类型标注。
from collections.abc import Iterable
# 用于根据文档更新时间计算新鲜度。
from datetime import datetime

# SQLAlchemy 会话对象。
from sqlalchemy.orm import Session

# 检索数量配置。
from app.config import RETRIEVE_TOP_K
# 数据模型：Chunk 为知识块，RetrievalLog 为检索日志。
from app.models import Chunk, RetrievalLog
# 向量召回入口。
from app.services.vector_store import query_chunks


# 匹配中文连续片段，或者英文/数字/下划线 token。
TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+")
# 向量召回候选池放大倍数。
VECTOR_CANDIDATE_MULTIPLIER = 3
# 关键词召回候选池放大倍数。
KEYWORD_CANDIDATE_MULTIPLIER = 4
# 综合相关性阈值。
RELEVANCE_SCORE_THRESHOLD = 0.50
# 关键词回退阈值。
RELEVANCE_KEYWORD_FALLBACK_THRESHOLD = 0.20
# 向量回退阈值。
RELEVANCE_VECTOR_FALLBACK_THRESHOLD = 0.55


def _flatten(items: list[list] | None) -> list:
    # Chroma 类结果常见格式是 [[...]]，这里统一摊平成普通列表。
    if not items:
        return []
    return list(items[0])


def _distance_to_score(distance: float | int | None) -> float:
    # 向量库返回的是 distance，越小越相关。
    # 这里转成 score，越大越相关，便于和关键词分数融合。
    if distance is None:
        return 0.0
    score = 1 - float(distance)
    return round(max(0.0, min(1.0, score)), 4)


def _chunk_ids_from_metadata(metadatas: Iterable[dict | None]) -> list[int]:
    # 从向量召回的 metadata 中提取 chunk_id。
    chunk_ids: list[int] = []
    for metadata in metadatas:
        if not metadata:
            continue
        chunk_id = metadata.get("chunk_id")
        if chunk_id is None:
            continue
        chunk_ids.append(int(chunk_id))
    return chunk_ids


def tokenize(text: str) -> list[str]:
    # token 列表。
    tokens: list[str] = []
    # 统一转小写后进行轻量拆分。
    for token in TOKEN_PATTERN.findall(text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            # 中文片段先拆单字。
            tokens.extend(list(token))
            # 再补双字 gram，提升中文关键词召回效果。
            if len(token) > 1:
                tokens.extend(token[index:index + 2] for index in range(len(token) - 1))
        else:
            # 英文和数字 token 直接保留。
            tokens.append(token)
    return tokens


def _cosine_similarity(query_tokens: list[str], text_tokens: list[str]) -> float:
    # 任一侧为空，相似度直接为 0。
    if not query_tokens or not text_tokens:
        return 0.0
    query_counts = Counter(query_tokens)
    text_counts = Counter(text_tokens)
    # 点积。
    dot = sum(query_counts[token] * text_counts[token] for token in query_counts)
    # 范数。
    norm_q = math.sqrt(sum(value * value for value in query_counts.values()))
    norm_t = math.sqrt(sum(value * value for value in text_counts.values()))
    if not norm_q or not norm_t:
        return 0.0
    return dot / (norm_q * norm_t)


def _keyword_score(query: str, query_tokens: list[str], chunk: Chunk) -> tuple[float, dict[str, float | int]]:
    # 标题和正文统一转小写。
    title = (chunk.document.title or "").lower()
    content = (chunk.content or "").lower()
    # 分词。
    title_tokens = tokenize(title)
    content_tokens = tokenize(content)

    # 统计正文命中数。
    overlap_count = sum(1 for token in set(query_tokens) if token in content_tokens)
    # 统计标题命中数。
    title_overlap_count = sum(1 for token in set(query_tokens) if token in title_tokens)
    # 正文命中比例。
    overlap_ratio = overlap_count / max(len(set(query_tokens)), 1)
    # 标题命中比例。
    title_overlap_ratio = title_overlap_count / max(len(set(query_tokens)), 1)
    # 稀疏词袋余弦相似度。
    cosine = _cosine_similarity(query_tokens, content_tokens)

    # query 整体短语是否直接出现在正文中。
    phrase_hit = 1 if query.lower() in content else 0
    # query 整体短语是否直接出现在标题中。
    title_phrase_hit = 1 if query.lower() in title else 0

    # 组合多个词法特征得到关键词召回得分。
    score = (
        0.35 * overlap_ratio
        + 0.20 * min(overlap_count / 6, 1.0)
        + 0.20 * cosine
        + 0.15 * title_overlap_ratio
        + 0.05 * phrase_hit
        + 0.05 * title_phrase_hit
    )

    # 额外返回详细特征，后续重排时继续使用。
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
    # 没有更新时间时不给新鲜度加分。
    if updated_at is None:
        return 0.0
    # 计算距离现在多少天。
    days = max((datetime.utcnow() - updated_at).days, 0)
    if days <= 7:
        return 0.03
    if days <= 30:
        return 0.02
    if days <= 90:
        return 0.01
    return 0.0


def _keyword_recall(db: Session, query: str, query_tokens: list[str], top_k: int) -> list[dict]:
    # 只从 ready 文档中召回 chunk。
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

    # 关键词召回先按词法信号排序。
    candidates.sort(
        key=lambda item: (
            item["keyword_score"],
            item["features"]["title_overlap_count"],
            item["features"]["keyword_overlap_count"],
        ),
        reverse=True,
    )

    # 返回放大后的候选池，而不是直接 top_k。
    return candidates[: top_k * KEYWORD_CANDIDATE_MULTIPLIER]


def _vector_recall(db: Session, query: str, top_k: int) -> list[dict]:
    # 从向量库召回更大的候选池。
    chroma_result = query_chunks(query=query, top_k=top_k * VECTOR_CANDIDATE_MULTIPLIER)
    metadatas = _flatten(chroma_result.get("metadatas"))
    documents = _flatten(chroma_result.get("documents"))
    distances = _flatten(chroma_result.get("distances"))

    chunk_ids = _chunk_ids_from_metadata(metadatas)
    if not chunk_ids:
        return []

    # 再回数据库查询 chunk 和文档状态，保证结果可控。
    chunk_rows = db.query(Chunk).join(Chunk.document).filter(Chunk.id.in_(chunk_ids)).all()
    chunk_map = {chunk.id: chunk for chunk in chunk_rows if chunk.document.status == "ready"}

    candidates: list[dict] = []
    for metadata, content, distance in zip(metadatas, documents, distances):
        if not metadata or metadata.get("chunk_id") is None:
            continue
        chunk = chunk_map.get(int(metadata["chunk_id"]))
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
                "content": content or chunk.content,
            }
        )
    return candidates


def _merge_candidates(keyword_candidates: list[dict], vector_candidates: list[dict]) -> list[dict]:
    # 用 chunk.id 合并关键词召回和向量召回结果。
    merged: dict[int, dict] = {}

    for candidate in keyword_candidates + vector_candidates:
        chunk = candidate["chunk"]
        current = merged.get(chunk.id)
        if current is None:
            merged[chunk.id] = candidate
            continue

        # 合并命中渠道。
        current["channels"].update(candidate["channels"])
        # 保留更高的关键词得分。
        current["keyword_score"] = max(current["keyword_score"], candidate["keyword_score"])
        # 保留更高的向量得分。
        current["vector_score"] = max(current["vector_score"], candidate["vector_score"])
        # 距离保留更小值。
        if current["distance"] is None or (
            candidate["distance"] is not None and candidate["distance"] < current["distance"]
        ):
            current["distance"] = candidate["distance"]
        # 如果候选自带内容，则更新内容。
        if candidate["content"]:
            current["content"] = candidate["content"]
        # 合并特征字典。
        current["features"].update(candidate["features"])

    return list(merged.values())


def _rerank_candidates(candidates: list[dict]) -> list[dict]:
    # 用于保存最终重排后的候选列表。
    reranked: list[dict] = []

    for candidate in candidates:
        chunk = candidate["chunk"]
        features = candidate["features"]
        keyword_score = candidate["keyword_score"]
        vector_score = candidate["vector_score"]
        # 标题命中加分。
        title_hit_bonus = 0.08 if features.get("title_overlap_count", 0) else 0.0
        # 关键词和向量同时命中，额外加分。
        multi_channel_bonus = 0.12 if len(candidate["channels"]) >= 2 else 0.0
        # 依据更新时间增加少量新鲜度分。
        freshness_bonus = _freshness_score(chunk.document.updated_at)

        # 计算融合后的最终分数。
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

    # 按综合分和多个辅助信号排序。
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
    # 综合分足够高，直接通过。
    if float(candidate.get("score") or 0.0) >= RELEVANCE_SCORE_THRESHOLD:
        return True
    # 标题命中也可以放行。
    if bool(candidate.get("title_hit")):
        return True
    # 关键词和向量同时达到兜底阈值时，也允许通过。
    if (
        float(candidate.get("keyword_score") or 0.0) >= RELEVANCE_KEYWORD_FALLBACK_THRESHOLD
        and float(candidate.get("vector_score") or 0.0) >= RELEVANCE_VECTOR_FALLBACK_THRESHOLD
    ):
        return True
    return False


def retrieve(db: Session, session_id: int | None, query: str, top_k: int = RETRIEVE_TOP_K) -> list[dict]:
    # 第一步：对 query 做轻量分词。
    query_tokens = tokenize(query)
    # 第二步：关键词召回。
    keyword_candidates = _keyword_recall(db, query, query_tokens, top_k)
    # 第三步：向量召回。
    vector_candidates = _vector_recall(db, query, top_k)
    # 第四步：合并两路候选。
    merged_candidates = _merge_candidates(keyword_candidates, vector_candidates)
    # 第五步：融合重排。
    reranked_candidates = _rerank_candidates(merged_candidates)

    # 记录未截断前的候选快照，用于日志和排障。
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

    # 第六步：相关性过滤。
    filtered_candidates = [candidate for candidate in reranked_candidates if _passes_relevance_gate(candidate)]
    # 第七步：截取最终 top_k。
    top_results = filtered_candidates[:top_k]

    # 将本次检索过程写入检索日志。
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
