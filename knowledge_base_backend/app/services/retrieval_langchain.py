from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from collections import Counter
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import (
    RERANKER_CE_FLOOR,
    RERANKER_ENABLED,
    RERANKER_MAX_CANDIDATES,
    RERANKER_SCORE_WEIGHT,
    RETRIEVE_TOP_K,
)
from app.models import Chunk, Document, RetrievalLog
from app.qa_logging import emit_qa_log
from app.schemas import QueryUnderstandingResult
from app.services.langchain_runtime import get_reranker
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
GENERIC_COVERAGE_TERMS = {
    *LOW_VALUE_COVERAGE_TERMS,
    "物联网卡",
    "物联卡",
    "开卡",
    "修改",
    "订单",
}
PRIMARY_SOURCE_QUOTAS = {
    "raw_query": 2,
    "rewrite_query": 1,
}
EXPANSION_SOURCE_QUOTAS = {
    "search_query": 2,
    "search_term": 1,
}
# 查询规格限流：问题理解会产出大量同义规格，全部重放检索成本过高。
# 这里按来源优先级去重后截断，兼顾改写问句、扩展问句和关键词命中。
MAX_QUERY_SPECS = 8
QUERY_SPEC_SOURCE_PRIORITY = [
    "rewrite_query",
    "raw_query",
    "search_query",
    "keyword",
    "search_term",
    "entity",
]
# 关键词召回预筛候选数：先用分词缓存做轻量打分粗排，再对前 N 个候选执行精确打分。
KEYWORD_PREFILTER_LIMIT = 480
# 分词记忆容量上限，防止长尾文本无限占用内存。
TOKENIZE_MEMO_LIMIT = 8192


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


def _build_retrieval_log_summary(items: list[dict]) -> dict[str, object]:
    documents = _unique_keep_order([str(item.get("document_title") or "").strip() for item in items])
    return {
        "chunk_count": len(items),
        "documents": documents,
    }


def _log_retrieval_selection(
    items: list[dict],
) -> None:
    summary = _build_retrieval_log_summary(items)
    emit_qa_log(
        "检索知识库",
        f"检索分片数={summary['chunk_count']} | 检索文档={_to_json(summary['documents'])}",
    )


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


# ---------------------------------------------------------------------------
# 分词缓存与关键词召回索引
#
# 原实现对每一路查询都全量加载 ready chunks 并逐条重新分词打分，
# 在 2 万+ chunk、多路查询规格下会成为检索链路的主要耗时。
# 这里引入两层缓存：
# 1. `_tokenize_memo`：按原文缓存分词结果，重复出现的查询和候选 chunk 不再重复分词；
# 2. `_keyword_cache`：按当前 ready chunk 集合一次性构建 token id 集合，
#    查询时只做集合求交粗排，精确打分只发生在少量预筛候选上。
# 缓存版本用 (ready chunk 数量, 最大 chunk id) 标识，
# 文档增删、重建、chunk 编辑/删除路径也会主动调用 invalidate_keyword_cache。
# ---------------------------------------------------------------------------

_keyword_cache_lock = threading.Lock()
_keyword_cache: dict = {"version": None, "vocab": {}, "content_tokens": {}, "title_tokens": {}}
_tokenize_memo: dict[str, list[str]] = {}


def tokenize_cached(text: str) -> list[str]:
    normalized = str(text or "").lower()
    cached = _tokenize_memo.get(normalized)
    if cached is None:
        cached = tokenize(normalized)
        if len(_tokenize_memo) >= TOKENIZE_MEMO_LIMIT:
            _tokenize_memo.clear()
        _tokenize_memo[normalized] = cached
    return cached


def invalidate_keyword_cache() -> None:
    """文档/chunk 发生变更时调用，强制下一次检索重建关键词缓存。"""

    global _keyword_cache
    with _keyword_cache_lock:
        _keyword_cache = {"version": None, "vocab": {}, "content_tokens": {}, "title_tokens": {}}


def _ready_chunk_version(db: Session) -> tuple[int, int]:
    row = (
        db.query(func.count(Chunk.id), func.max(Chunk.id))
        .join(Chunk.document)
        .filter(Document.status == "ready")
        .one()
    )
    return int(row[0] or 0), int(row[1] or 0)


def _to_token_id_set(tokens: list[str], vocab: dict[str, int]) -> frozenset[int]:
    ids: set[int] = set()
    for token in set(tokens):
        token_id = vocab.get(token)
        if token_id is None:
            token_id = len(vocab)
            vocab[token] = token_id
        ids.add(token_id)
    return frozenset(ids)


def _build_keyword_cache(db: Session, version: tuple[int, int]) -> dict:
    started = time.time()
    rows = (
        db.query(Chunk.id, Chunk.content, Document.title)
        .join(Chunk.document)
        .filter(Document.status == "ready")
        .all()
    )
    vocab: dict[str, int] = {}
    content_tokens: dict[int, frozenset[int]] = {}
    title_tokens: dict[int, frozenset[int]] = {}
    for chunk_id, content, title in rows:
        content_tokens[chunk_id] = _to_token_id_set(tokenize_cached(content or ""), vocab)
        title_tokens[chunk_id] = _to_token_id_set(tokenize_cached(title or ""), vocab)
    logger.info(
        "[retrieval.keyword_cache] rebuilt chunks=%s vocab=%s elapsed=%.2fs",
        len(rows),
        len(vocab),
        time.time() - started,
    )
    return {
        "version": version,
        "vocab": vocab,
        "content_tokens": content_tokens,
        "title_tokens": title_tokens,
    }


def _get_keyword_cache(db: Session) -> dict:
    global _keyword_cache
    version = _ready_chunk_version(db)
    cache = _keyword_cache
    if cache["version"] == version and cache["content_tokens"]:
        return cache
    with _keyword_cache_lock:
        if _keyword_cache["version"] != version or not _keyword_cache["content_tokens"]:
            _keyword_cache = _build_keyword_cache(db, version)
        return _keyword_cache


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
    title_tokens = tokenize_cached(title)
    content_tokens = tokenize_cached(content)

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
    title_tokens = tokenize_cached(title)
    content_tokens = tokenize_cached(content)
    term_tokens = tokenize_cached(normalized_term)
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
    cache = _get_keyword_cache(db)
    vocab = cache["vocab"]
    content_tokens = cache["content_tokens"]
    title_tokens = cache["title_tokens"]

    query_token_ids = {vocab[token] for token in set(query_tokens) if token in vocab}
    if not query_token_ids:
        return []

    # 第一遍：基于分词缓存做集合求交粗排，避免对全量 chunk 重新分词打分。
    rough: list[tuple[int, int, int]] = []
    for chunk_id, chunk_token_ids in content_tokens.items():
        overlap_count = len(query_token_ids & chunk_token_ids)
        title_overlap_count = len(query_token_ids & title_tokens.get(chunk_id, frozenset()))
        if overlap_count or title_overlap_count:
            rough.append((overlap_count, title_overlap_count, chunk_id))
    if not rough:
        return []
    rough.sort(reverse=True)
    candidate_ids = [chunk_id for _, _, chunk_id in rough[:KEYWORD_PREFILTER_LIMIT]]

    # 第二遍：只对预筛候选执行原有精确打分，保持打分语义不变。
    rows = (
        db.query(Chunk)
        .join(Chunk.document)
        .filter(Chunk.id.in_(candidate_ids), Chunk.document.has(status="ready"))
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
        try:
            chunk_id = int(chunk_id)
        except (TypeError, ValueError):
            continue

        chunk = (
            db.query(Chunk)
            .join(Chunk.document)
            .filter(Chunk.id == chunk_id, Chunk.document.has(status="ready"))
            .first()
        )
        vector_id = metadata.get("chroma_id") or getattr(langchain_doc, "id", None)
        # SQLite may reuse IDs after rollback/deletion. Never trust an orphan vector's chunk_id alone.
        if chunk is None or vector_id != chunk.chroma_id:
            continue

        candidates.append(
            {
                "chunk": chunk,
                "keyword_score": 0.0,
                "vector_score": _distance_to_score(distance),
                "distance": round(float(distance), 4) if distance is not None else None,
                "channels": {"vector"},
                "features": {},
                "content": chunk.content,
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
                # 排序专用未截断分：若直接用截断后的 score 排序，大量域内候选会同时
                # 顶到 1.0 上限形成超长并列组，重排加成带来的细微优势会被完全抹平，
                # 导致“最全面的目标块”在并列组中随机沉底、被覆盖词补位块挤出。
                "rank_score": final_score,
                "keyword_score": round(keyword_score, 4),
                "vector_score": round(vector_score, 4),
                "distance": candidate["distance"],
                "channels": sorted(candidate["channels"]),
                "title_hit": bool(features.get("title_overlap_count", 0)),
                "freshness_bonus": round(freshness_bonus, 4),
                "understanding_bonus": round(understanding_bonus, 4),
                "is_structure_only": is_structure_only,
                "query_sources": sorted(candidate.get("query_sources") or []),
                "source_queries": candidate.get("source_queries") or [],
                "source_ranks": candidate.get("source_ranks") or {},
                "best_source": candidate.get("best_source"),
                "chroma_id": chunk.chroma_id,
            }
        )

    reranked.sort(
        key=lambda item: (
            item.get("rank_score") or item["score"],
            len(item["channels"]),
            item["title_hit"],
            item["vector_score"],
            item["keyword_score"],
        ),
        reverse=True,
    )
    return reranked


def _cross_encoder_rerank(candidates: list[dict], query: str) -> list[dict]:
    """交叉编码器精排：对启发式粗排后的候选做 (query, chunk) 联合相关性打分。

    只对前 RERANKER_MAX_CANDIDATES 个候选执行（CPU 推理成本可控）。
    打分模式为“只升不降”的有界加成：仅当 reranker_score 超过 RERANKER_CE_FLOOR
    时按比例加成，绝不因模型低分而扣减启发式得分——
    实测 bge-reranker-base 对同文档内共享关键词的块打分趋同、对长合并块易低估，
    替换式融合会把原本高分的长块压到相关性门槛之下，反而破坏召回覆盖。
    任一环节失败时降级返回原始顺序，保证检索链路不因重排模型异常而中断。
    """

    if not RERANKER_ENABLED or not candidates:
        return candidates
    normalized_query = str(query or "").strip()
    if not normalized_query:
        return candidates

    scored_candidates = candidates[:RERANKER_MAX_CANDIDATES]
    untouched_candidates = candidates[RERANKER_MAX_CANDIDATES:]
    started = time.time()
    try:
        reranker = get_reranker()
        texts = [_candidate_text(candidate) for candidate in scored_candidates]
        raw_scores = reranker.predict(
            [(normalized_query, text) for text in texts],
            batch_size=16,
            show_progress_bar=False,
        )
    except Exception:
        logger.warning("[retrieval.reranker] failed; keep heuristic order", exc_info=True)
        return candidates

    for candidate, raw_score in zip(scored_candidates, raw_scores):
        reranker_score = max(0.0, min(float(raw_score), 1.0))
        heuristic_rank = float(candidate.get("rank_score") or candidate.get("score") or 0.0)
        bonus = RERANKER_SCORE_WEIGHT * max(0.0, reranker_score - RERANKER_CE_FLOOR)
        candidate["heuristic_score"] = round(min(heuristic_rank, 1.0), 4)
        candidate["reranker_score"] = round(reranker_score, 4)
        candidate["reranker_bonus"] = round(bonus, 4)
        # 展示分保持 [0,1]，排序分允许超过 1 以保留加成带来的区分度。
        candidate["score"] = round(min(heuristic_rank + bonus, 1.0), 4)
        candidate["rank_score"] = heuristic_rank + bonus

    reranked = scored_candidates + untouched_candidates
    # 与 _rerank_candidates 的复合排序键保持一致，仅插入 reranker 相关字段作为次级键。
    reranked.sort(
        key=lambda item: (
            item.get("rank_score") or item.get("score") or 0.0,
            item.get("reranker_bonus") or 0.0,
            len(item["channels"]),
            item["title_hit"],
            item["vector_score"],
            item["keyword_score"],
        ),
        reverse=True,
    )
    logger.info(
        "[retrieval.reranker] scored=%s elapsed=%.2fs top_reranker_score=%s",
        len(scored_candidates),
        time.time() - started,
        reranked[0].get("reranker_score") if reranked else None,
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
    specific_terms = [term for term in unique_terms if term not in GENERIC_COVERAGE_TERMS]
    fallback_terms = [term for term in unique_terms if term in GENERIC_COVERAGE_TERMS]
    specific_terms.sort(key=len, reverse=True)
    return specific_terms + fallback_terms[:2]


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


def _source_rank(candidate: dict, source: str) -> int:
    ranks = candidate.get("source_ranks") or {}
    try:
        return int(ranks.get(source, 999999))
    except (TypeError, ValueError):
        return 999999


def _candidate_source_score(candidate: dict, source: str) -> tuple:
    source_queries = " ".join(str(query) for query in candidate.get("source_queries") or [])
    content = str(candidate.get("content") or "")
    source_query_hit = 1 if source_queries and any(part and part in content for part in source_queries.split()) else 0
    return (
        float(candidate.get("rank_score") or candidate.get("score") or 0.0),
        source_query_hit,
        -_source_rank(candidate, source),
        float(candidate.get("keyword_score") or 0.0),
        float(candidate.get("vector_score") or 0.0),
    )


def _pick_from_source_bucket(
    candidates: list[dict],
    source: str,
    quota: int,
    selected_ids: set[int],
) -> list[dict]:
    if quota <= 0:
        return []

    source_candidates = [
        candidate
        for candidate in candidates
        if source in set(candidate.get("query_sources") or [])
        and int(candidate.get("chunk_id") or 0) not in selected_ids
    ]
    source_candidates.sort(key=lambda candidate: _candidate_source_score(candidate, source), reverse=True)
    picked: list[dict] = []
    for candidate in source_candidates:
        picked.append(candidate)
        selected_ids.add(int(candidate.get("chunk_id") or 0))
        if len(picked) >= quota:
            break
    return picked


def _select_bucketed_top_results(
    reranked_candidates: list[dict],
    top_k: int,
    coverage_terms: list[str] | None = None,
) -> list[dict]:
    if top_k <= 0:
        return []
    eligible_candidates = [candidate for candidate in reranked_candidates if _passes_relevance_gate(candidate)]
    selected: list[dict] = []
    selected_ids: set[int] = set()

    for source, quota in PRIMARY_SOURCE_QUOTAS.items():
        for candidate in _pick_from_source_bucket(eligible_candidates, source, quota, selected_ids):
            selected.append(candidate)
            if len(selected) >= top_k:
                return selected

    for term in _coverage_terms(coverage_terms or []):
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

    for source, quota in EXPANSION_SOURCE_QUOTAS.items():
        for candidate in _pick_from_source_bucket(eligible_candidates, source, quota, selected_ids):
            selected.append(candidate)
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


def _query_specs_from_understanding(understanding: QueryUnderstandingResult) -> list[tuple[str, str]]:
    specs: list[tuple[str, str]] = [
        ("rewrite_query", understanding.rewrite_query),
        ("raw_query", understanding.raw_query),
    ]
    specs.extend(("keyword", term) for term in understanding.keywords_hit)
    specs.extend(("entity", term) for term in understanding.entities)
    specs.extend(("search_query", query) for query in getattr(understanding, "search_queries", []))
    specs.extend(("search_term", term) for term in getattr(understanding, "search_terms", []))

    # 跨来源按归一化值去重：同一个词以 keyword/entity/search_term 等多身份出现时，
    # 只保留来源优先级最高的一条，避免同义规格重复重放检索。
    priority = {source: index for index, source in enumerate(QUERY_SPEC_SOURCE_PRIORITY)}
    best_by_value: dict[str, tuple[str, str]] = {}
    insertion_order: list[str] = []
    for source, query in specs:
        value = str(query or "").strip()
        if not value:
            continue
        normalized = re.sub(r"\s+", "", value.lower())
        existing = best_by_value.get(normalized)
        if existing is None:
            best_by_value[normalized] = (source, value)
            insertion_order.append(normalized)
        elif priority.get(source, len(QUERY_SPEC_SOURCE_PRIORITY)) < priority.get(
            existing[0], len(QUERY_SPEC_SOURCE_PRIORITY)
        ):
            best_by_value[normalized] = (source, value)

    deduped = [best_by_value[normalized] for normalized in insertion_order]
    deduped.sort(key=lambda item: priority.get(item[0], len(QUERY_SPEC_SOURCE_PRIORITY)))
    return deduped[:MAX_QUERY_SPECS]


def _tag_candidate_source(candidate: dict, *, source: str, query: str, rank: int) -> dict:
    candidate["query_sources"] = {source}
    candidate["source_queries"] = [query]
    candidate["source_ranks"] = {source: rank}
    candidate["best_source"] = source
    return candidate


def _merge_sourced_candidate(merged_candidates: dict[int, dict], candidate: dict) -> dict:
    chunk = candidate["chunk"]
    existing = merged_candidates.get(chunk.id)
    if existing is None:
        merged_candidates[chunk.id] = candidate
        return candidate

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

    existing.setdefault("query_sources", set()).update(candidate.get("query_sources") or [])
    source_queries = existing.setdefault("source_queries", [])
    for source_query in candidate.get("source_queries") or []:
        if source_query not in source_queries:
            source_queries.append(source_query)

    source_ranks = existing.setdefault("source_ranks", {})
    for source, rank in (candidate.get("source_ranks") or {}).items():
        current_rank = source_ranks.get(source)
        source_ranks[source] = rank if current_rank is None else min(current_rank, rank)

    best_source = existing.get("best_source")
    if best_source is None or _source_rank(existing, str(best_source)) > min(source_ranks.values()):
        existing["best_source"] = min(source_ranks, key=source_ranks.get)

    return existing


def retrieve(db: Session, session_id: int | None, query: str, top_k: int = RETRIEVE_TOP_K) -> list[dict]:
    query_tokens = tokenize(query)
    keyword_candidates = _keyword_recall(db, query, query_tokens, top_k)
    vector_candidates = _vector_recall(db, query, top_k)
    merged_candidates = _merge_candidates(keyword_candidates, vector_candidates)
    reranked_candidates = _rerank_candidates(merged_candidates)
    reranked_candidates = _cross_encoder_rerank(reranked_candidates, query)
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
    _log_retrieval_selection(top_results)

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
    query_specs = _query_specs_from_understanding(understanding)
    if not query_specs:
        query_specs = [("raw_query", understanding.raw_query)]

    merged_candidates: dict[int, dict] = {}
    retrieved_snapshot: list[dict] = []
    recall_top_k = max(top_k * 2, 16)

    for source, query in query_specs:
        query_tokens = tokenize(query)
        keyword_candidates = _keyword_recall(db, query, query_tokens, recall_top_k)
        vector_candidates = _vector_recall(db, query, recall_top_k)
        per_query_candidates = _merge_candidates(keyword_candidates, vector_candidates)

        for rank, candidate in enumerate(per_query_candidates, start=1):
            _tag_candidate_source(candidate, source=source, query=query, rank=rank)
            chunk = candidate["chunk"]
            _merge_sourced_candidate(merged_candidates, candidate)

            retrieved_snapshot.append(
                {
                    "source": source,
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

    understanding_terms = _unique_keep_order(
        [
            *getattr(understanding, "search_terms", []),
            *getattr(understanding, "search_queries", []),
            *understanding.keywords_hit,
            *understanding.entities,
        ]
    )
    reranked_candidates = _rerank_candidates(list(merged_candidates.values()), understanding_terms=understanding_terms)
    reranked_candidates = _cross_encoder_rerank(
        reranked_candidates, understanding.rewrite_query.strip() or understanding.raw_query
    )
    filtered_candidates = [candidate for candidate in reranked_candidates if _passes_relevance_gate(candidate)]
    top_results = _select_bucketed_top_results(reranked_candidates, top_k, coverage_terms=understanding_terms)
    _log_retrieval_selection(top_results)

    db.add(
        RetrievalLog(
            session_id=session_id,
            query=json.dumps(
                {
                    "raw_query": understanding.raw_query,
                    "rewrite_query": understanding.rewrite_query,
                    "keywords": understanding.keywords_hit,
                    "entities": understanding.entities,
                    "search_terms": getattr(understanding, "search_terms", []),
                    "search_queries": getattr(understanding, "search_queries", []),
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
