from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

import httpx

from app.config import (
    FOLLOW_UP_ENABLED,
    FOLLOW_UP_MAX_TURNS,
    OLLAMA_BASE_URL,
    QUERY_UNDERSTANDING_ENABLED,
    QUERY_UNDERSTANDING_FALLBACK_MODEL,
    QUERY_UNDERSTANDING_FALLBACK_PROVIDER,
    QUERY_UNDERSTANDING_MODEL,
    QUERY_UNDERSTANDING_PROVIDER,
    QUERY_UNDERSTANDING_TIMEOUT_SECONDS,
    SEMANTIC_CONFIDENCE_THRESHOLD,
)
from app.schemas import QueryUnderstandingResult
from app.services.minimax import chat_completion


BUSINESS_KEYWORDS_STRONG = [
    "物联网集中化支撑系统CMIOT",
    "物联网卡",
    "AI灵技助手",
    "集团客户注册",
    "集团成员批量开户",
    "集团群组",
    "和对讲",
    "宽带网",
    "5GNSA",
    "5G SA",
    "车务通后装",
    "前装",
    "国际出口",
    "M2M业务",
    "OneNet",
    "千里眼",
    "贴片卡终端换货",
    "CAT.1套餐订购",
    "账户红名单",
    "云视讯",
    "号卡资源",
    "高风险合同",
    "技术管控商品",
    "增值商品操作",
    "个人客户用户批量销户",
    "业务统一管理",
    "客户白名单",
    "成员过户",
    "实名制信息",
    "个人成员开户",
    "合账代付",
    "物联网业务预警",
    "统一工作台",
    "5G健康度和5G资费",
    "SIM卡费用",
    "资源管理中心",
    "预受理",
    "角色配置",
    "流量共享",
    "业务办理资料",
    "菜单权限",
    "车联网卡",
    "360视图操作",
    "客户级商品",
    "通信服务类",
    "联合惩戒黑名单",
    "预开预存发票",
]

BUSINESS_KEYWORDS_GENERAL = [
    "操作手册",
    "项目管理",
    "开户",
    "销户",
    "缴费周期",
    "黑名单信息",
    "缴费",
    "调账",
    "退款",
    "冲销",
    "挂账",
    "转账",
    "欠费",
    "电子账单",
    "销售",
    "合账",
    "逾期欠费",
    "呆坏账",
    "余额迁转",
    "计费常见问题",
    "营业常见问题",
    "实名认证",
    "成员",
    "套餐",
]

FOLLOW_UP_CUES = [
    "这个",
    "那个",
    "那",
    "这",
    "这边",
    "么",
    "然后",
    "那如果",
    "那要是",
    "那超过",
    "线上也能",
    "也能办吗",
    "怎么处理",
    "怎么办",
    "一样吗",
    "还能",
    "可以吗",
]

OUT_OF_SCOPE_CUES = [
    "天气",
    "气温",
    "下雨",
    "新闻",
    "股票",
    "股价",
    "彩票",
    "写首诗",
    "写代码",
    "讲笑话",
    "今天几号",
    "汇率",
]

ALLOWED_ROUTES = {"kb_qa", "out_of_scope"}
JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
RULE_SEARCH_TERM_CANDIDATES = [
    "物联网卡",
    "物联卡",
    "开卡",
    "开户",
    "批量开户",
    "主商品",
    "主产品",
    "增值商品",
    "账户模式",
    "测试期",
    "折扣",
    "缴费周期",
    "政府类",
    "政府部门",
    "企业类型",
    "后付费",
    "订单成功",
    "订单",
    "修改",
    "变更",
    "重新修改",
    "选择",
]
ENRICHMENT_CUES = [
    "能",
    "能不能",
    "可以",
    "是否",
    "怎么",
    "如何",
    "设置",
    "选择",
    "选错",
    "修改",
    "变更",
    "报错",
    "限制",
    "规则",
    "政策",
]


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


def _normalize_match_text(text: str) -> str:
    return re.sub(r"[\s_\-:/]+", "", text or "").lower()


def _derive_rule_search_terms(query: str, keyword_hits: list[str]) -> list[str]:
    normalized = query or ""
    terms = [term for term in RULE_SEARCH_TERM_CANDIDATES if term in normalized]

    if "开卡" in normalized:
        terms.append("开户")
    if "改" in normalized or "变更" in normalized:
        terms.append("修改")
    if "选错" in normalized:
        terms.extend(["选择", "修改"])
    if "主商品" in normalized and any(marker in normalized for marker in ["改", "修改", "变更", "选错"]):
        terms.extend(["主商品修改", "主产品修改", "重新修改主产品"])

    terms.extend(keyword_hits)
    return _unique_keep_order(terms)


def _derive_rule_search_queries(query: str, search_terms: list[str]) -> list[str]:
    normalized = query or ""
    terms = set(search_terms)
    queries: list[str] = []

    if "主商品" in terms and any(term in terms for term in {"修改", "变更", "重新修改"}):
        queries.extend(
            [
                "主商品 批量开户 订单成功 修改 主产品",
                "主商品设置 批量开户 无法重新修改主产品",
                "主产品 重新修改",
            ]
        )

    if "测试期" in terms:
        queries.extend(["SIM卡 初始状态 可测试 测试期", "测试期免费资源用尽后处理动作"])
    if "折扣" in terms:
        queries.extend(["批量开户 设置折扣 是否打折", "商品折扣率 折扣价格"])
    if "账户模式" in terms:
        queries.extend(["账户模式 集团统付 单卡个付", "账户信息 账户模式"])
    if "政府类" in terms or "政府部门" in terms:
        queries.extend(["企业类型 政府部门 后付费 缴费周期", "政府部门 缴费周期 12个月"])

    if not queries and len(normalized) >= 8:
        queries.append(normalized)
    return _unique_keep_order(queries)


def _safe_extract_json(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    if not stripped:
        return {}
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        match = JSON_OBJECT_RE.search(stripped)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}


def _clamp_confidence(value: Any, default: float = 0.0) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, round(confidence, 4)))


class QueryUnderstandingService:
    def __init__(self) -> None:
        self.enabled = QUERY_UNDERSTANDING_ENABLED
        self.provider = QUERY_UNDERSTANDING_PROVIDER
        self.model = QUERY_UNDERSTANDING_MODEL
        self.fallback_provider = QUERY_UNDERSTANDING_FALLBACK_PROVIDER
        self.fallback_model = QUERY_UNDERSTANDING_FALLBACK_MODEL
        self.follow_up_enabled = FOLLOW_UP_ENABLED
        self.follow_up_max_turns = max(FOLLOW_UP_MAX_TURNS, 1)
        self.semantic_confidence_threshold = max(0.0, min(SEMANTIC_CONFIDENCE_THRESHOLD, 1.0))
        self.timeout_seconds = max(5.0, QUERY_UNDERSTANDING_TIMEOUT_SECONDS)

    def detect_keywords(self, query: str) -> dict[str, Any]:
        strong_hits = [keyword for keyword in BUSINESS_KEYWORDS_STRONG if self._keyword_in_query(keyword, query)]
        general_hits = [keyword for keyword in BUSINESS_KEYWORDS_GENERAL if self._keyword_in_query(keyword, query)]
        all_hits = _unique_keep_order(strong_hits + general_hits)
        score = min(1.0, len(strong_hits) * 0.32 + len(general_hits) * 0.12)
        return {
            "strong_hits": strong_hits,
            "general_hits": general_hits,
            "all_hits": all_hits,
            "score": round(score, 4),
            "domain_confident": bool(strong_hits) or len(all_hits) >= 2,
        }

    def detect_follow_up_by_rules(self, query: str, history: list[dict[str, Any]]) -> dict[str, Any]:
        normalized = (query or "").strip()
        short_query = len(normalized) <= 18
        cue_hits = [cue for cue in FOLLOW_UP_CUES if cue in normalized]
        punctuation_only = normalized in {"?", "？", "嗯？", "然后呢", "还有吗"}
        previous_user = self._last_user_message(history)
        is_candidate = self.follow_up_enabled and bool(history) and (
            punctuation_only
            or (short_query and bool(cue_hits))
            or normalized.startswith(("那", "这", "这个", "那个", "然后", "线上"))
        )
        return {
            "is_follow_up_candidate": is_candidate,
            "need_context": bool(is_candidate and previous_user),
            "cue_hits": cue_hits,
            "previous_user_query": previous_user,
            "confidence": 0.82 if is_candidate else 0.12,
        }

    def detect_route_by_rules(
        self,
        query: str,
        history: list[dict[str, Any]],
        session_summary: dict[str, Any],
        keyword_hits: dict[str, Any],
        follow_up: dict[str, Any],
    ) -> dict[str, Any]:
        del history
        normalized = (query or "").strip()
        previous_understanding = session_summary.get("query_understanding") or {}
        previous_route = str(previous_understanding.get("route") or session_summary.get("last_route") or "")
        previous_topic_query = str(
            previous_understanding.get("rewrite_query")
            or session_summary.get("last_user_question")
            or follow_up.get("previous_user_query")
            or ""
        ).strip()

        if keyword_hits["strong_hits"]:
            return {
                "route": "kb_qa",
                "confidence": 0.96,
                "reason": "strong_business_keyword_hit",
                "need_model": False,
                "previous_topic_query": previous_topic_query,
            }

        if keyword_hits["domain_confident"]:
            return {
                "route": "kb_qa",
                "confidence": 0.88,
                "reason": "multiple_business_keyword_hit",
                "need_model": False,
                "previous_topic_query": previous_topic_query,
            }

        if any(cue in normalized for cue in OUT_OF_SCOPE_CUES) and not keyword_hits["all_hits"]:
            return {
                "route": "out_of_scope",
                "confidence": 0.92,
                "reason": "obvious_out_of_scope",
                "need_model": False,
                "previous_topic_query": previous_topic_query,
            }

        if follow_up["is_follow_up_candidate"] and previous_route == "kb_qa":
            return {
                "route": "kb_qa",
                "confidence": 0.78,
                "reason": "follow_up_after_kb_qa",
                "need_model": True,
                "previous_topic_query": previous_topic_query,
            }

        return {
            "route": "out_of_scope",
            "confidence": 0.35,
            "reason": "rules_uncertain",
            "need_model": True,
            "previous_topic_query": previous_topic_query,
        }

    def understand(
        self,
        *,
        query: str,
        history: list[dict[str, Any]],
        session_summary: dict[str, Any] | None = None,
    ) -> QueryUnderstandingResult:
        raw_query = (query or "").strip()
        session_summary = session_summary or {}

        keyword_hits = self.detect_keywords(raw_query)
        follow_up = self.detect_follow_up_by_rules(raw_query, history)
        route_rules = self.detect_route_by_rules(raw_query, history, session_summary, keyword_hits, follow_up)
        rule_result = self._build_rule_result(raw_query, keyword_hits, follow_up, route_rules)

        if not self.enabled:
            return rule_result

        if not route_rules["need_model"]:
            if not self._needs_model_enrichment(raw_query, route_rules):
                return rule_result
            model_result = self.understand_with_fallback(
                query=raw_query,
                history=history,
                session_summary=session_summary,
                keyword_hits=keyword_hits,
                follow_up=follow_up,
                route_rules=route_rules,
            )
            if model_result is None:
                return rule_result
            return self._merge_rule_and_model_result(rule_result, model_result, keyword_hits, follow_up, route_rules)

        model_result = self.understand_with_fallback(
            query=raw_query,
            history=history,
            session_summary=session_summary,
            keyword_hits=keyword_hits,
            follow_up=follow_up,
            route_rules=route_rules,
        )
        if model_result is None:
            return rule_result
        if model_result.confidence < self.semantic_confidence_threshold:
            return rule_result
        return self._merge_rule_and_model_result(rule_result, model_result, keyword_hits, follow_up, route_rules)

    def _needs_model_enrichment(self, query: str, route_rules: dict[str, Any]) -> bool:
        if route_rules.get("route") != "kb_qa":
            return False
        normalized = (query or "").strip()
        return len(normalized) >= 8 and any(cue in normalized for cue in ENRICHMENT_CUES)

    def understand_with_ollama(
        self,
        *,
        query: str,
        history: list[dict[str, Any]],
        session_summary: dict[str, Any],
        keyword_hits: dict[str, Any],
        follow_up: dict[str, Any],
        route_rules: dict[str, Any],
        fallback_used: bool,
    ) -> QueryUnderstandingResult:
        prompt = self._build_prompt(query, history, session_summary, keyword_hits, follow_up, route_rules)
        response = httpx.post(
            f"{OLLAMA_BASE_URL.rstrip('/')}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        parsed = _safe_extract_json(str(payload.get("response") or ""))
        return self._result_from_model_payload(
            parsed,
            raw_query=query,
            provider="ollama",
            model=self.model,
            fallback_used=fallback_used,
        )

    def understand_with_minimax(
        self,
        *,
        query: str,
        history: list[dict[str, Any]],
        session_summary: dict[str, Any],
        keyword_hits: dict[str, Any],
        follow_up: dict[str, Any],
        route_rules: dict[str, Any],
        fallback_used: bool,
    ) -> QueryUnderstandingResult:
        prompt = self._build_prompt(query, history, session_summary, keyword_hits, follow_up, route_rules)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是知识库问答系统的语义理解模块。"
                    "你只能输出合法 JSON，不能输出额外解释。"
                ),
            },
            {"role": "user", "content": prompt},
        ]
        text = chat_completion(messages, model=self.fallback_model, temperature=0, max_output_tokens=1024)
        parsed = _safe_extract_json(text)
        return self._result_from_model_payload(
            parsed,
            raw_query=query,
            provider="minimax",
            model=self.fallback_model,
            fallback_used=fallback_used,
        )

    def understand_with_fallback(
        self,
        *,
        query: str,
        history: list[dict[str, Any]],
        session_summary: dict[str, Any],
        keyword_hits: dict[str, Any],
        follow_up: dict[str, Any],
        route_rules: dict[str, Any],
    ) -> QueryUnderstandingResult | None:
        providers = [self.provider]
        if self.fallback_provider and self.fallback_provider not in providers:
            providers.append(self.fallback_provider)

        for index, provider in enumerate(providers):
            try:
                if provider == "ollama":
                    return self.understand_with_ollama(
                        query=query,
                        history=history,
                        session_summary=session_summary,
                        keyword_hits=keyword_hits,
                        follow_up=follow_up,
                        route_rules=route_rules,
                        fallback_used=index > 0,
                    )
                if provider == "minimax":
                    return self.understand_with_minimax(
                        query=query,
                        history=history,
                        session_summary=session_summary,
                        keyword_hits=keyword_hits,
                        follow_up=follow_up,
                        route_rules=route_rules,
                        fallback_used=index > 0,
                    )
            except Exception:
                continue
        return None

    def _build_rule_result(
        self,
        raw_query: str,
        keyword_hits: dict[str, Any],
        follow_up: dict[str, Any],
        route_rules: dict[str, Any],
    ) -> QueryUnderstandingResult:
        rewrite_query = self._heuristic_rewrite_query(
            raw_query=raw_query,
            previous_topic_query=route_rules.get("previous_topic_query", ""),
            previous_user_query=follow_up.get("previous_user_query", ""),
            is_follow_up=bool(follow_up["is_follow_up_candidate"]),
        )
        search_terms = _derive_rule_search_terms(raw_query, keyword_hits["all_hits"])
        return QueryUnderstandingResult(
            route=route_rules["route"],
            confidence=_clamp_confidence(route_rules["confidence"]),
            is_follow_up=bool(follow_up["is_follow_up_candidate"]),
            need_context=bool(follow_up["need_context"] and route_rules["route"] == "kb_qa"),
            raw_query=raw_query,
            rewrite_query=rewrite_query,
            keywords_hit=keyword_hits["all_hits"],
            entities=keyword_hits["all_hits"][:6],
            search_terms=search_terms,
            search_queries=_derive_rule_search_queries(raw_query, search_terms),
            filters={},
            reason=route_rules["reason"],
            provider="rules",
            model="rules",
            fallback_used=False,
        )

    def _merge_rule_and_model_result(
        self,
        rule_result: QueryUnderstandingResult,
        model_result: QueryUnderstandingResult,
        keyword_hits: dict[str, Any],
        follow_up: dict[str, Any],
        route_rules: dict[str, Any],
    ) -> QueryUnderstandingResult:
        route = model_result.route
        if keyword_hits["strong_hits"]:
            route = "kb_qa"
        elif follow_up["is_follow_up_candidate"] and route_rules["reason"] == "follow_up_after_kb_qa":
            route = "kb_qa"

        rewrite_query = self._stabilize_rewrite_query(rewrite_query=model_result.rewrite_query, rule_result=rule_result)
        return QueryUnderstandingResult(
            route=route,
            confidence=max(model_result.confidence, rule_result.confidence),
            is_follow_up=model_result.is_follow_up or rule_result.is_follow_up,
            need_context=model_result.need_context or rule_result.need_context,
            raw_query=rule_result.raw_query,
            rewrite_query=rewrite_query,
            keywords_hit=_unique_keep_order(rule_result.keywords_hit + model_result.keywords_hit),
            entities=_unique_keep_order(model_result.entities + rule_result.entities),
            search_terms=_unique_keep_order(model_result.search_terms + rule_result.search_terms),
            search_queries=_unique_keep_order(model_result.search_queries + rule_result.search_queries),
            filters=model_result.filters or rule_result.filters,
            reason=model_result.reason or rule_result.reason,
            provider=model_result.provider,
            model=model_result.model,
            fallback_used=model_result.fallback_used,
        )

    def _result_from_model_payload(
        self,
        payload: dict[str, Any],
        *,
        raw_query: str,
        provider: str,
        model: str,
        fallback_used: bool,
    ) -> QueryUnderstandingResult:
        route = str(payload.get("route") or "out_of_scope").strip()
        if route not in ALLOWED_ROUTES:
            route = "out_of_scope"
        rewrite_query = str(payload.get("rewrite_query") or raw_query).strip() or raw_query
        return QueryUnderstandingResult(
            route=route,
            confidence=_clamp_confidence(payload.get("confidence"), default=0.0),
            is_follow_up=bool(payload.get("is_follow_up", False)),
            need_context=bool(payload.get("need_context", False)),
            raw_query=raw_query,
            rewrite_query=rewrite_query,
            keywords_hit=_unique_keep_order([str(item) for item in payload.get("keywords_hit") or []]),
            entities=_unique_keep_order([str(item) for item in payload.get("entities") or []]),
            search_terms=_unique_keep_order([str(item) for item in payload.get("search_terms") or []]),
            search_queries=_unique_keep_order([str(item) for item in payload.get("search_queries") or []]),
            filters=payload.get("filters") if isinstance(payload.get("filters"), dict) else {},
            reason=str(payload.get("reason") or ""),
            provider=provider,
            model=model,
            fallback_used=fallback_used,
        )

    def _stabilize_rewrite_query(
        self,
        *,
        rewrite_query: str,
        rule_result: QueryUnderstandingResult,
    ) -> str:
        candidate = (rewrite_query or "").strip()
        if not candidate:
            return rule_result.rewrite_query or rule_result.raw_query
        if rule_result.is_follow_up and candidate == rule_result.raw_query:
            return rule_result.rewrite_query or candidate
        if rule_result.keywords_hit and not any(self._keyword_in_query(keyword, candidate) for keyword in rule_result.keywords_hit):
            return rule_result.rewrite_query or rule_result.raw_query
        return candidate

    def _heuristic_rewrite_query(
        self,
        *,
        raw_query: str,
        previous_topic_query: str,
        previous_user_query: str,
        is_follow_up: bool,
    ) -> str:
        if not is_follow_up:
            return raw_query
        base_query = (previous_topic_query or previous_user_query or "").strip()
        if not base_query:
            return raw_query
        if raw_query in base_query:
            return base_query
        return f"{base_query}；补充追问：{raw_query}"

    def _build_prompt(
        self,
        query: str,
        history: list[dict[str, Any]],
        session_summary: dict[str, Any],
        keyword_hits: dict[str, Any],
        follow_up: dict[str, Any],
        route_rules: dict[str, Any],
    ) -> str:
        recent_history = [
            {
                "role": item.get("role", "user"),
                "content": str(item.get("content") or "")[:400],
            }
            for item in history[-self.follow_up_max_turns :]
        ]
        compact_summary = {
            "topic": session_summary.get("topic"),
            "last_user_question": session_summary.get("last_user_question"),
            "last_answer_summary": session_summary.get("last_answer_summary"),
            "last_route": session_summary.get("last_route")
            or (session_summary.get("query_understanding") or {}).get("route"),
        }
        instructions = {
            "task": [
                "判断当前问题是否属于知识库问答范围",
                "判断是否是追问",
                "判断是否需要继承上下文",
                "生成适合检索的 rewrite_query",
                "抽取关键词和实体",
            ],
            "constraints": [
                "不要随意替换业务术语",
                "如果不确定，降低 confidence",
                "如果当前问题是追问，rewrite_query 要补全上下文但不能改掉用户原意",
                "如果明显不是知识库问题，route 必须是 out_of_scope",
                "只输出 JSON 对象，不允许输出额外说明",
            ],
            "allowed_routes": ["kb_qa", "out_of_scope"],
            "json_schema": {
                "route": "kb_qa | out_of_scope",
                "confidence": "0-1 float",
                "is_follow_up": "bool",
                "need_context": "bool",
                "rewrite_query": "string",
                "keywords_hit": ["string"],
                "entities": ["string"],
                "search_terms": ["string"],
                "search_queries": ["string"],
                "filters": {},
                "reason": "string",
            },
        }
        instructions["task"].append(
            "Generate search_terms for retrieval expansion: include source-document wording, synonyms, key conditions, values, and business nouns that may not appear in rewrite_query"
        )
        instructions["task"].append(
            "Generate search_queries as 2-5 short focused retrieval queries that combine the core object, action, state, and possible document wording"
        )
        instructions["constraints"].append(
            "search_terms must be short retrieval terms, not full sentences; do not invent facts, but include plausible document terms for the same business meaning"
        )
        instructions["constraints"].append(
            "search_queries should be concise keyword queries, not answers; keep raw user intent and possible source wording both represented"
        )
        payload = {
            "business_keywords": _unique_keep_order(BUSINESS_KEYWORDS_STRONG + BUSINESS_KEYWORDS_GENERAL),
            "current_query": query,
            "recent_history": recent_history,
            "session_summary": compact_summary,
            "rule_signals": {
                "keyword_hits": keyword_hits,
                "follow_up": follow_up,
                "route_rules": route_rules,
            },
            "instructions": instructions,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @staticmethod
    def _keyword_in_query(keyword: str, query: str) -> bool:
        if not keyword or not query:
            return False
        if keyword.lower() in query.lower():
            return True
        return _normalize_match_text(keyword) in _normalize_match_text(query)

    @staticmethod
    def _last_user_message(history: list[dict[str, Any]]) -> str:
        for item in reversed(history):
            if item.get("role") == "user":
                return str(item.get("content") or "").strip()
        return ""


@lru_cache(maxsize=1)
def get_query_understanding_service() -> QueryUnderstandingService:
    return QueryUnderstandingService()
