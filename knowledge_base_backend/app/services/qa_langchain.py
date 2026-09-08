from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterator

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableSerializable

from app.config import QA_HISTORY_LIMIT, QA_MAX_CONTEXT_CHARS
from app.qa_logging import emit_qa_log
from app.services.langchain_runtime import get_chat_llm


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _to_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _build_evidence_log_summary(evidence: list[dict[str, Any]]) -> dict[str, object]:
    documents: list[str] = []
    for item in evidence:
        document = str(item.get("document_title") or "").strip()
        if document and document not in documents:
            documents.append(document)
    return {
        "chunk_count": len(evidence),
        "documents": documents,
    }


def _log_evidence_selection(evidence: list[dict[str, Any]]) -> None:
    summary = _build_evidence_log_summary(evidence)
    emit_qa_log(
        "分片过滤",
        f"过滤后分片数={summary['chunk_count']} | 过滤后文档={_to_json(summary['documents'])}",
    )


@dataclass(slots=True)
class SemanticAnalysis:
    """语义分析结果。

    当前为了保持业务行为稳定，仍然优先使用“直接检索用户原问题”的策略。
    但保留这个结构体，可以让后续继续扩展 LangChain 的 Query Rewrite、
    Follow-up Resolve、Structured Output 等能力，而不用推翻现有接口。
    """

    intent: str = "qa"
    route: str = "kb_qa"
    raw_query: str = ""
    is_follow_up: bool = False
    rewrite_query: str = ""
    keywords_hit: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)
    needs_retrieval: bool = True
    risk_level: str = "medium"
    memory_summary: str = ""
    reason: str = ""
    confidence: float = 0.0


def trim_text(text: str, max_chars: int) -> str:
    normalized = text.strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def load_history_messages(db, session_id: int, limit: int = QA_HISTORY_LIMIT) -> list[dict[str, Any]]:
    from app.models import Message

    rows = (
        db.query(Message)
        .filter(Message.session_id == session_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "role": row.role,
            "content": row.content,
            "references": json.loads(row.references_json) if row.references_json else [],
        }
        for row in reversed(rows)
    ]


def parse_session_summary(summary_json: str | None) -> dict[str, Any]:
    if not summary_json:
        return {}
    try:
        parsed = json.loads(summary_json)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def build_memory_summary(session_summary: dict[str, Any], recent_messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []

    topic = session_summary.get("topic")
    if topic:
        parts.append(f"topic: {topic}")

    last_user_question = session_summary.get("last_user_question")
    if last_user_question:
        parts.append(f"last_user_question: {last_user_question}")

    last_answer_summary = session_summary.get("last_answer_summary")
    if last_answer_summary:
        parts.append(f"last_answer_summary: {trim_text(str(last_answer_summary), 240)}")

    last_route = session_summary.get("last_route")
    if last_route:
        parts.append(f"last_route: {last_route}")

    key_entities = session_summary.get("key_entities") or []
    if key_entities:
        parts.append(f"key_entities: {', '.join(str(item) for item in key_entities[:10])}")

    if not parts:
        for item in recent_messages[-4:]:
            role = item.get("role", "user")
            content = trim_text(str(item.get("content", "")), 160)
            parts.append(f"{role}: {content}")

    return "\n".join(parts)


def _format_retrieval_item(index: int, item: dict[str, Any]) -> str:
    title = str(item.get("document_title") or "untitled").strip()
    content = trim_text(str(item.get("content") or ""), 1200)
    score = item.get("score")
    keyword_score = item.get("keyword_score")
    vector_score = item.get("vector_score")
    channels = item.get("channels") or []
    return "\n".join(
        [
            f"[{index}]",
            f"Title: {title}",
            f"Score: {score}",
            f"KeywordScore: {keyword_score}",
            f"VectorScore: {vector_score}",
            f"Channels: {', '.join(channels) if channels else 'unknown'}",
            f"Content: {content}",
        ]
    )


def build_evidence_context(
    retrieval_results: list[dict[str, Any]],
    max_chars: int = QA_MAX_CONTEXT_CHARS,
) -> tuple[str, list[dict[str, Any]]]:
    """把检索结果拼成给 LLM 使用的证据上下文。

    这一步仍然由项目自己掌控，而不是直接把 `Document` 原样塞给模型。
    原因是企业知识库场景通常需要：

    1. 明确地展示引用来源和得分。
    2. 控制上下文长度预算。
    3. 产出可以直接返给前端的结构化引用数据。
    """
    blocks: list[str] = []
    evidence: list[dict[str, Any]] = []
    total = 0

    for index, item in enumerate(retrieval_results, start=1):
        block = _format_retrieval_item(index, item)
        next_total = total + len(block) + 2
        if blocks and next_total > max_chars:
            break

        blocks.append(block)
        evidence.append(
            {
                "evidence_id": index,
                "chunk_id": item.get("chunk_id"),
                "document_id": item.get("document_id"),
                "document_title": item.get("document_title"),
                "chunk_index": item.get("chunk_index"),
                "section_title": item.get("section_title"),
                "chapter_path": item.get("chapter_path"),
                "snippet": trim_text(str(item.get("content") or ""), 280),
                "full_content": trim_text(str(item.get("content") or ""), 6000),
                "score": item.get("score"),
                "keyword_score": item.get("keyword_score"),
                "vector_score": item.get("vector_score"),
                "channels": item.get("channels") or [],
            }
        )
        total = next_total

    if not blocks:
        _log_evidence_selection([])
        return "", []
    evidence_context = "\n\n".join(blocks)
    _log_evidence_selection(evidence)
    return evidence_context, evidence


def build_history_block(recent_messages: list[dict[str, Any]]) -> str:
    if not recent_messages:
        return "(empty)"

    lines: list[str] = []
    for item in recent_messages:
        role = item.get("role", "user")
        content = trim_text(str(item.get("content", "")), 320)
        lines.append(f"- {role}: {content}")
    return "\n".join(lines)


def _build_answer_prompt() -> ChatPromptTemplate:
    """构造 LangChain ChatPromptTemplate。

    这是本次问答改造的核心之一：Prompt 不再手工拼成消息数组，而是交给
    LangChain 的 PromptTemplate 管理。这样变量占位更清晰，后续扩展也更容易。
    """

    system_prompt = "\n".join(
        [
            "你是企业知识库问答助手。",
            "只能依据提供的证据和会话上下文回答。",
            "严格紧扣问题的提问范围作答：只回答用户明确问到的内容。",
            "不要主动补充问题未涉及的场景例外、特殊情形或替代方案；即使证据中包含这些信息也不要写入答案。",
            "如果证据不足，请明确说明证据不足，不要编造。",
            "如果多个证据冲突，请指出冲突，并优先采用更完整或更新的版本。",
            "回答总字数控制在 400 字以内：结论优先，只保留与问题直接相关的关键信息，省略背景铺垫和无关展开。",
            "请使用 Markdown 输出。",
            "优先采用以下结构：",
            "### 结论",
            "### 依据",
            "### 注意事项",
            "### 后续问题",
            "每个小节只保留最关键的条目；若某小节无内容可写，直接省略该小节。",
            "段落尽量简短，多点信息请使用项目符号。",
        ]
    )

    human_prompt = "\n".join(
        [
            "原始问题：",
            "{question}",
            "",
            "意图：{intent}",
            "路由：{route}",
            "是否追问：{is_follow_up}",
            "检索查询：{rewrite_query}",
            "命中关键词：{keywords_hit}",
            "识别实体：{entities}",
            "",
            "会话记忆：",
            "{memory_summary}",
            "",
            "近期对话：",
            "{recent_history}",
            "",
            "证据：",
            "{evidence_context}",
        ]
    )

    return ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("human", human_prompt),
        ]
    )


def build_answer_chain() -> RunnableSerializable[dict[str, Any], str]:
    """构建 LangChain Runnable 链。

    `PromptTemplate -> ChatOpenAI -> StrOutputParser`
    是 LangChain 中最常见、最容易理解的基础链式结构。
    """

    prompt = _build_answer_prompt()
    llm = get_chat_llm()
    parser = StrOutputParser()
    return prompt | llm | parser


def _build_answer_chain_inputs(
    *,
    question: str,
    analysis: SemanticAnalysis,
    memory_summary: str,
    recent_messages: list[dict[str, Any]],
    evidence_context: str,
) -> dict[str, str]:
    return {
        "question": question,
        "intent": analysis.intent,
        "route": analysis.route,
        "is_follow_up": str(analysis.is_follow_up).lower(),
        "rewrite_query": analysis.rewrite_query or question,
        "keywords_hit": ", ".join(analysis.keywords_hit) if analysis.keywords_hit else "(none)",
        "entities": ", ".join(analysis.entities) if analysis.entities else "(none)",
        "memory_summary": memory_summary or "(empty)",
        "recent_history": build_history_block(recent_messages),
        "evidence_context": evidence_context or "(no evidence)",
    }


def _render_prompt_messages(prompt: ChatPromptTemplate, inputs: dict[str, str]) -> list[dict[str, str]]:
    return [
        {"role": message.type, "content": str(message.content)}
        for message in prompt.format_messages(**inputs)
    ]


def _render_prompt_text(prompt_messages: list[dict[str, str]]) -> str:
    return "\n\n".join(f"[{item['role']}]\n{item['content']}" for item in prompt_messages)


def invoke_answer_chain(
    *,
    question: str,
    analysis: SemanticAnalysis,
    memory_summary: str,
    recent_messages: list[dict[str, Any]],
    evidence_context: str,
) -> str:
    inputs = _build_answer_chain_inputs(
        question=question,
        analysis=analysis,
        memory_summary=memory_summary,
        recent_messages=recent_messages,
        evidence_context=evidence_context,
    )
    prompt = _build_answer_prompt()
    prompt_messages = _render_prompt_messages(prompt, inputs)
    emit_qa_log("整理答案", _render_prompt_text(prompt_messages))

    chain = prompt | get_chat_llm() | StrOutputParser()
    output = chain.invoke(inputs)
    return output


def stream_answer_chain(
    *,
    question: str,
    analysis: SemanticAnalysis,
    memory_summary: str,
    recent_messages: list[dict[str, Any]],
    evidence_context: str,
) -> Iterator[str]:
    """流式执行同一条 LangChain Runnable 链。"""

    inputs = _build_answer_chain_inputs(
        question=question,
        analysis=analysis,
        memory_summary=memory_summary,
        recent_messages=recent_messages,
        evidence_context=evidence_context,
    )
    prompt = _build_answer_prompt()
    prompt_messages = _render_prompt_messages(prompt, inputs)
    emit_qa_log("整理答案", _render_prompt_text(prompt_messages))

    chain = prompt | get_chat_llm() | StrOutputParser()
    chunks: list[str] = []
    try:
        for chunk in chain.stream(inputs):
            if chunk:
                chunks.append(str(chunk))
            yield chunk
    except Exception:
        logger.exception(
            "[qa.llm.response] mode=stream status=error chunk_count=%s partial_output=%s",
            len(chunks),
            "".join(chunks),
        )
        raise


_SUMMARY_RE = re.compile(r"(?ms)^###\s*(?:摘要|补充说明|注意事项|Notes)\s*$\n(.*?)(?=^###\s|\Z)")
_FOLLOWUP_RE = re.compile(r"(?ms)^###\s*(?:后续问题|追问建议|Follow-up)\s*$\n(.*?)(?=^###\s|\Z)")


def extract_answer_summary(answer_text: str) -> str:
    match = _SUMMARY_RE.search(answer_text or "")
    if match:
        content = trim_text(match.group(1).strip(), 400)
        if content:
            return content

    first_line = next((line.strip() for line in (answer_text or "").splitlines() if line.strip()), "")
    return trim_text(first_line, 240)


def extract_followup_questions(answer_text: str) -> list[str]:
    match = _FOLLOWUP_RE.search(answer_text or "")
    if not match:
        return []

    block = match.group(1)
    questions: list[str] = []
    for line in block.splitlines():
        text = re.sub(r"^[\-*\d.\s]+", "", line.strip())
        if text:
            questions.append(text)
    return questions[:3]


def build_session_summary(
    *,
    question: str,
    analysis: SemanticAnalysis,
    answer_text: str,
    retrieval_results: list[dict[str, Any]],
    query_understanding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = {
        "topic": analysis.intent,
        "last_user_question": question,
        "last_answer_summary": extract_answer_summary(answer_text),
        "key_entities": analysis.entities[:10],
        "rewrite_query": analysis.rewrite_query,
        "retrieved_count": len(retrieval_results),
        "confidence": 0.7 if retrieval_results else 0.2,
        "last_route": analysis.route,
    }
    if query_understanding:
        summary["query_understanding"] = query_understanding
    return summary
