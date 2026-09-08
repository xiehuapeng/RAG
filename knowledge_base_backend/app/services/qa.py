from __future__ import annotations

# 用于解析和生成 JSON 结构，例如 session.summary_json。
import json
# 用于从回答文本中提取摘要区块和追问建议区块。
import re
# dataclass 便于定义语义分析结果对象。
from dataclasses import dataclass, field
# Any 仅用于宽松类型标注，适合处理数据库记录和动态字典。
from typing import Any

# 读取问答链路相关配置。
from app.config import (
    LLM_MAX_OUTPUT_TOKENS,
    LLM_MODEL_NAME,
    LLM_REASONING_SPLIT,
    LLM_TEMPERATURE,
    QA_HISTORY_LIMIT,
    QA_MAX_CONTEXT_CHARS,
)


@dataclass(slots=True)
class SemanticAnalysis:
    # 当前问题的意图类型。现在默认固定为 qa。
    intent: str = "qa"
    # 是否属于追问。当前直接检索方案中固定为 False。
    is_follow_up: bool = False
    # 用于检索的 query。当前直接等于用户原问题。
    rewrite_query: str = ""
    # 识别出的实体列表。当前直接检索方案中默认为空。
    entities: list[str] = field(default_factory=list)
    # 是否需要检索。当前问答链路固定为 True。
    needs_retrieval: bool = True
    # 风险等级，供后续扩展更严格的提示词控制。
    risk_level: str = "medium"
    # 模型生成的记忆摘要。当前直接检索方案中不使用。
    memory_summary: str = ""


def normalize_text(text: str) -> str:
    # 将多个空白字符压缩成一个空格，提升检索 query 稳定性。
    return " ".join(text.split()).strip()


def trim_text(text: str, max_chars: int) -> str:
    # 先去掉首尾空白。
    normalized = text.strip()
    # 如果长度未超预算，直接返回。
    if len(normalized) <= max_chars:
        return normalized
    # 超出预算时截断，并在末尾补省略号。
    return normalized[: max_chars - 3].rstrip() + "..."


def load_history_messages(db, session_id: int, limit: int = QA_HISTORY_LIMIT) -> list[dict[str, Any]]:
    # 局部导入，避免服务层之间形成循环依赖。
    from app.models import Message

    # 查询当前会话最近若干条消息。
    rows = (
        db.query(Message)
        .filter(Message.session_id == session_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
        .all()
    )
    # 数据库查出来是倒序，这里反转成时间正序，便于放入 prompt。
    return [
        {
            "role": row.role,
            "content": row.content,
            "references": json.loads(row.references_json) if row.references_json else [],
        }
        for row in reversed(rows)
    ]


def parse_session_summary(summary_json: str | None) -> dict[str, Any]:
    # 没有摘要时直接返回空字典。
    if not summary_json:
        return {}
    try:
        # 尝试解析 JSON 文本。
        parsed = json.loads(summary_json)
        # 只有字典才是合法的摘要结构。
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        # 遇到脏数据时容错，不阻塞问答流程。
        return {}


def build_memory_summary(session_summary: dict[str, Any], recent_messages: list[dict[str, Any]]) -> str:
    # parts 用于拼装给回答模型看的“压缩记忆”。
    parts: list[str] = []

    # 读取话题。
    topic = session_summary.get("topic")
    if topic:
        parts.append(f"topic: {topic}")

    # 读取上一轮用户问题。
    last_user_question = session_summary.get("last_user_question")
    if last_user_question:
        parts.append(f"last_user_question: {last_user_question}")

    # 读取上一轮助手回答摘要。
    last_assistant_answer = session_summary.get("last_assistant_answer")
    if last_assistant_answer:
        parts.append(f"last_assistant_answer: {trim_text(str(last_assistant_answer), 240)}")

    # 读取关键实体。
    key_entities = session_summary.get("key_entities") or []
    if key_entities:
        parts.append(f"key_entities: {', '.join(str(item) for item in key_entities[:10])}")

    # 如果没有结构化摘要，就退化为最近几轮原始消息。
    if not parts:
        for item in recent_messages[-4:]:
            role = item.get("role", "user")
            content = trim_text(str(item.get("content", "")), 160)
            parts.append(f"{role}: {content}")

    # 按换行拼接，形成最终 memory。
    return "\n".join(parts)


def build_semantic_prompt(question: str, memory_summary: str, recent_messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    # 虽然当前主链路已经不再调用“语义理解 LLM”，但保留这个函数，
    # 便于后续如果恢复 query 改写时直接复用。
    history_lines = []
    for item in recent_messages:
        role = item.get("role", "user")
        content = trim_text(str(item.get("content", "")), 240)
        history_lines.append(f"- {role}: {content}")

    user_content = "\n".join(
        [
            "You are a query understanding module for a knowledge base QA system.",
            "Return only valid JSON with the following keys:",
            "intent, is_follow_up, rewrite_query, entities, needs_retrieval, risk_level, memory_summary",
            "Classify the user request, resolve pronouns from history, and rewrite the question into a standalone retrieval query.",
            "",
            f"Session memory:\n{memory_summary or '(empty)'}",
            "",
            f"Recent history:\n" + ("\n".join(history_lines) if history_lines else "(empty)"),
            "",
            f"Current question:\n{question}",
        ]
    )

    return [
        {
            "role": "system",
            "content": "You are a precise query understanding engine. You must answer with JSON only and no markdown.",
        },
        {
            "role": "user",
            "content": user_content,
        },
    ]


def _extract_json_object(text: str) -> dict[str, Any]:
    # 先去掉首尾空白。
    stripped = text.strip()
    # 空字符串直接返回空字典。
    if not stripped:
        return {}
    try:
        # 优先尝试把完整文本当作 JSON 解析。
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        # 失败后继续走兜底提取。
        pass

    # 提取最外层大括号包裹的 JSON 对象。
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}

    candidate = stripped[start : end + 1]
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def parse_semantic_analysis(text: str, question: str) -> SemanticAnalysis:
    # 从模型文本中解析 JSON 对象。
    payload = _extract_json_object(text)
    # 取 rewrite_query，没有则回退到原问题。
    rewrite_query = str(payload.get("rewrite_query") or question).strip()
    # 取实体列表。
    entities = payload.get("entities") or []
    if not isinstance(entities, list):
        entities = []

    # 构造成结构化对象返回。
    return SemanticAnalysis(
        intent=str(payload.get("intent") or "qa"),
        is_follow_up=bool(payload.get("is_follow_up", False)),
        rewrite_query=rewrite_query or question,
        entities=[str(item) for item in entities if str(item).strip()],
        needs_retrieval=bool(payload.get("needs_retrieval", True)),
        risk_level=str(payload.get("risk_level") or "medium"),
        memory_summary=str(payload.get("memory_summary") or ""),
    )


def build_retrieval_query(analysis: SemanticAnalysis, question: str) -> str:
    # 优先使用改写 query；如果没有，则直接用原问题。
    query = normalize_text(analysis.rewrite_query or question)
    if query:
        return query
    return normalize_text(question)


def _format_retrieval_item(index: int, item: dict[str, Any]) -> str:
    # 读取文档标题，缺失时使用 untitled。
    title = str(item.get("document_title") or "untitled").strip()
    # 读取 chunk 内容，并做较长截断，控制 prompt 大小。
    content = trim_text(str(item.get("content") or ""), 1200)
    # 读取综合分、关键词分和向量分。
    score = item.get("score")
    keyword_score = item.get("keyword_score")
    vector_score = item.get("vector_score")
    # 读取命中渠道。
    channels = item.get("channels") or []
    # 格式化成给回答模型看的证据块文本。
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


def build_evidence_context(retrieval_results: list[dict[str, Any]], max_chars: int = QA_MAX_CONTEXT_CHARS) -> tuple[str, list[dict[str, Any]]]:
    # blocks 是拼给回答模型看的证据文本。
    blocks: list[str] = []
    # evidence 是结构化引用列表，返回给前端和消息记录。
    evidence: list[dict[str, Any]] = []
    # total 用于控制总字符预算。
    total = 0

    # 按检索排序结果依次追加证据。
    for index, item in enumerate(retrieval_results, start=1):
        block = _format_retrieval_item(index, item)
        next_total = total + len(block) + 2
        # 如果不是第一块证据，并且加上后会超预算，则停止打包。
        if blocks and next_total > max_chars:
            break

        blocks.append(block)
        evidence.append(
            {
                "evidence_id": index,
                "chunk_id": item.get("chunk_id"),
                "document_id": item.get("document_id"),
                "document_title": item.get("document_title"),
                "snippet": trim_text(str(item.get("content") or ""), 280),
                "score": item.get("score"),
                "keyword_score": item.get("keyword_score"),
                "vector_score": item.get("vector_score"),
                "channels": item.get("channels") or [],
            }
        )
        total = next_total

    # 如果没有任何证据，则返回空内容和空列表。
    if not blocks:
        return "", []
    return "\n\n".join(blocks), evidence


def build_history_block(recent_messages: list[dict[str, Any]]) -> str:
    # 没有历史消息时返回空标记。
    if not recent_messages:
        return "(empty)"

    lines: list[str] = []
    for item in recent_messages:
        role = item.get("role", "user")
        content = trim_text(str(item.get("content", "")), 320)
        lines.append(f"- {role}: {content}")
    return "\n".join(lines)


def build_answer_prompt(
    question: str,
    analysis: SemanticAnalysis,
    memory_summary: str,
    recent_messages: list[dict[str, Any]],
    evidence_context: str,
) -> list[dict[str, str]]:
    # system prompt 用于强约束回答边界和输出格式。
    system_prompt = "\n".join(
        [
            "You are an enterprise knowledge base QA assistant.",
            "Answer only from the provided evidence and conversation memory.",
            "If the evidence is insufficient, say so explicitly.",
            "If multiple sources conflict, mention the conflict and prefer the most complete or latest one.",
            "Write in Markdown.",
            "Use explicit headings and leave blank lines between sections and paragraphs.",
            "Prefer this structure when possible:",
            "### Conclusion",
            "### Evidence",
            "### Notes",
            "### Follow-up",
            "Keep paragraphs short. Use bullet lists when there are multiple points.",
            "Do not fabricate knowledge outside the evidence.",
        ]
    )

    # user prompt 放入原问题、压缩记忆、最近几轮对话和证据上下文。
    user_prompt = "\n".join(
        [
            f"Original question:\n{question}",
            "",
            f"Detected intent: {analysis.intent}",
            f"Is follow-up: {str(analysis.is_follow_up).lower()}",
            f"Rewrite query: {analysis.rewrite_query}",
            f"Entities: {', '.join(analysis.entities) if analysis.entities else '(none)'}",
            f"Memory summary:\n{memory_summary or '(empty)'}",
            "",
            f"Recent history:\n{build_history_block(recent_messages)}",
            "",
            f"Evidence:\n{evidence_context or '(no evidence)'}",
        ]
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


# 用于从回答中提取 Notes 或摘要区块。
_SUMMARY_RE = re.compile(r"(?ms)^###\s*(?:摘要|补充说明|Notes)\s*$\n(.*?)(?=^###\s|\Z)")
# 用于从回答中提取 Follow-up 区块。
_FOLLOWUP_RE = re.compile(r"(?ms)^###\s*(?:追问建议|Follow-up)\s*$\n(.*?)(?=^###\s|\Z)")


def extract_answer_summary(answer_text: str) -> str:
    # 优先从专门的摘要区块中提取摘要。
    match = _SUMMARY_RE.search(answer_text or "")
    if match:
        content = trim_text(match.group(1).strip(), 400)
        if content:
            return content

    # 如果没有命中摘要区块，则退化为第一条非空行。
    first_line = next((line.strip() for line in (answer_text or "").splitlines() if line.strip()), "")
    return trim_text(first_line, 240)


def extract_followup_questions(answer_text: str) -> list[str]:
    # 查找 Follow-up 区块。
    match = _FOLLOWUP_RE.search(answer_text or "")
    if not match:
        return []

    block = match.group(1)
    questions: list[str] = []
    for line in block.splitlines():
        text = line.strip()
        # 去掉列表前缀，例如 -、*、1.
        text = re.sub(r"^[\-*\d.\s]+", "", text)
        if text:
            questions.append(text)
    # 最多只取前三条。
    return questions[:3]


def build_session_summary(
    *,
    question: str,
    analysis: SemanticAnalysis,
    answer_text: str,
    retrieval_results: list[dict[str, Any]],
) -> dict[str, Any]:
    # 将这一轮问答压缩成会话摘要，写入 ChatSession.summary_json。
    return {
        "topic": analysis.intent,
        "last_user_question": question,
        "last_answer_summary": extract_answer_summary(answer_text),
        "key_entities": analysis.entities[:10],
        "rewrite_query": analysis.rewrite_query,
        "retrieved_count": len(retrieval_results),
        "confidence": 0.7 if retrieval_results else 0.2,
    }


def build_generation_settings() -> dict[str, Any]:
    # extra_body 用于透传 LLM 的扩展参数。
    extra_body = {}
    if LLM_REASONING_SPLIT:
        extra_body["reasoning_split"] = True
    # 将模型相关配置统一集中返回，避免同步与流式路径不一致。
    return {
        "model": LLM_MODEL_NAME,
        "temperature": LLM_TEMPERATURE,
        "max_output_tokens": LLM_MAX_OUTPUT_TOKENS,
        "extra_body": extra_body,
    }
