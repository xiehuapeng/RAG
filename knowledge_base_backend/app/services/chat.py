from __future__ import annotations
import traceback
# 用于保存 references_json 和 summary_json。
import json
import re
# Any 用于宽松类型标注，例如流式 chunk 和 SSE payload。
from typing import Any

# FastAPI 的流式响应对象。
from fastapi.responses import StreamingResponse
# SQLAlchemy 会话对象。
from sqlalchemy.orm import Session

# 问答检索条数配置。
from app.config import QA_RETRIEVE_TOP_K
# 聊天相关数据模型。
from app.models import ChatSession, Feedback, Message, RetrievalLog
# MiniMax 客户端封装与配置异常。
from app.services.minimax import MinimaxConfigError
# 问答编排层函数。
from app.services.qa_langchain import (
    SemanticAnalysis,
    build_evidence_context,
    build_memory_summary,
    build_session_summary,
    extract_answer_summary,
    extract_followup_questions,
    invoke_answer_chain,
    load_history_messages,
    parse_session_summary,
    stream_answer_chain,
)
# 混合检索主入口。
from app.services.retrieval_langchain import retrieve


# 当知识库没有找到足够可靠的证据时，返回的兜底回答。
NO_ANSWER_TEXT = (
    "当前知识库里没有找到足够可靠的内容来回答这个问题。"
    "你可以换一种问法，或者补充更具体的场景、设备、流程或文档名称。"
)

THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"


# 聊天服务主流程集中在这个文件：
# - 管理会话生命周期
# - 调用检索与问答链
# - 生成普通回答或 SSE 流式回答
# - 在模型异常时提供兜底回复
def _friendly_stream_error(exc: Exception) -> tuple[str, str]:
    # 将内部异常映射为前端可识别的错误码和用户可读提示。
    message = str(exc).lower()
    if isinstance(exc, MinimaxConfigError):
        return "config_error", "模型配置缺失，请检查 API Key 和模型配置。"
    if "session not found" in message:
        return "session_not_found", "当前会话不存在，请刷新后重试。"
    if "session expired" in message or "401" in message:
        return "auth_error", "登录状态已失效，请重新登录。"
    if "timeout" in message or "timed out" in message:
        return "timeout", "模型响应时间过长，请稍后重试或缩短问题内容。"
    if "connection" in message or "connect" in message or "network" in message:
        return "network_error", "模型服务连接失败，请稍后重试。"
    return "chat_failed", "本次回答生成失败，请稍后重试。"


def _should_fallback_no_answer(retrieval_results: list[dict]) -> bool:
    # 没有任何检索结果时，直接兜底。
    if not retrieval_results:
        return True

    # 取第一条结果作为最强证据。
    top = retrieval_results[0]
    top_score = float(top.get("score") or 0.0)
    top_keyword = float(top.get("keyword_score") or 0.0)
    top_vector = float(top.get("vector_score") or 0.0)

    # 前三条里是否存在明显关键词命中。
    has_keyword_hit = any(float(item.get("keyword_score") or 0.0) >= 0.08 for item in retrieval_results[:3])
    # 前三条里是否存在标题命中。
    has_title_hit = any(bool(item.get("title_hit")) for item in retrieval_results[:3])

    # 综合分太低时，判定为证据不足。
    if top_score < 0.2:
        return True
    # 标题不命中、关键词不命中、向量分也偏低时，判定为证据不足。
    if not has_keyword_hit and not has_title_hit and top_vector < 0.55:
        return True
    # 综合分偏低且关键词分也低时，也兜底。
    if top_score < 0.3 and top_keyword < 0.05:
        return True
    return False


def build_answer(query: str, retrieval_results: list[dict]) -> tuple[str, list[dict], list[str]]:
    # 如果证据明显不足，则返回固定兜底文案和通用追问建议。
    if _should_fallback_no_answer(retrieval_results):
        return (
            NO_ANSWER_TEXT,
            [],
            ["这个问题对应的流程在哪里", "有没有更具体的操作条件", "是否有补充材料或上下文"],
        )

    # 否则选取前三条结果做简要拼接。
    references = retrieval_results[:3]
    title = references[0].get("document_title") or "知识库文档"
    summary_lines = [f"根据知识库检索结果，优先参考《{title}》："]
    for index, item in enumerate(references, start=1):
        excerpt = str(item.get("content") or "")[:120].replace("\n", " ")
        summary_lines.append(f"{index}. {excerpt}")
    summary_lines.append("如需更准确答复，可以继续补充设备、流程或异常场景。")
    suggestions = ["这个流程的前置条件是什么", "执行失败应该怎么处理", "还有没有补充材料"]
    return "\n".join(summary_lines), references, suggestions


def create_session(db: Session, user_id: int, title: str | None) -> ChatSession:
    # 创建新的聊天会话，标题为空时使用默认标题。
    # 创建新的聊天会话。
    session = ChatSession(user_id=user_id, title=title or "新建会话")
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def delete_session(db: Session, session: ChatSession) -> None:
    # 删除会话时，需要顺带清掉消息、反馈和检索日志，避免留下孤儿数据。
    # 先找出该会话下所有消息 id，便于删除反馈。
    message_ids = [message_id for (message_id,) in db.query(Message.id).filter(Message.session_id == session.id).all()]
    if message_ids:
        db.query(Feedback).filter(Feedback.message_id.in_(message_ids)).delete(synchronize_session=False)

    # 删除会话相关消息和检索日志。
    db.query(Message).filter(Message.session_id == session.id).delete(synchronize_session=False)
    db.query(RetrievalLog).filter(RetrievalLog.session_id == session.id).delete(synchronize_session=False)
    db.delete(session)
    db.commit()


def _build_memory_context(session: ChatSession, recent_messages: list[dict[str, Any]]) -> str:
    # 先解析会话压缩摘要，再与最近几轮消息拼接成记忆上下文。
    session_summary = parse_session_summary(session.summary_json)
    return build_memory_summary(session_summary, recent_messages)


def _load_prompt_messages(db: Session, session_id: int, current_content: str) -> list[dict[str, Any]]:
    # 读取最近消息。
    recent_messages = load_history_messages(db, session_id)
    # 如果最后一条用户消息就是当前刚保存的问题，则从历史中排除，避免重复喂给模型。
    if recent_messages and recent_messages[-1].get("role") == "user":
        last_content = str(recent_messages[-1].get("content") or "").strip()
        if last_content == current_content.strip():
            return recent_messages[:-1]
    return recent_messages


def _extract_delta_payload(chunk: Any) -> tuple[str, str]:
    # 从 OpenAI 兼容流式 chunk 中提取可识别的增量内容。
    # 返回值:
    # - ("content", 文本): 正常回答正文
    # - ("reasoning", 文本): 推理内容
    # - ("", ""): 无有效文本
    try:
        choice = chunk.choices[0]
        delta = choice.delta
        text = getattr(delta, "content", None)
        if text:
            return "content", str(text)
        reasoning_text = getattr(delta, "reasoning_content", None)
        if reasoning_text:
            return "reasoning", str(reasoning_text)
        reasoning_details = getattr(delta, "reasoning_details", None) or []
        detail_parts: list[str] = []
        for item in reasoning_details:
            if isinstance(item, dict):
                text = item.get("text")
            else:
                text = getattr(item, "text", None)
            if text:
                detail_parts.append(str(text))
        if detail_parts:
            return "reasoning", "".join(detail_parts)
    except Exception:
        return "", ""
    return "", ""


def _strip_hidden_reasoning(text: str) -> str:
    # 非流式回答里，如果模型把思考内容包在 <think>...</think> 中，这里统一清理掉。
    cleaned = re.sub(r"(?is)<think>.*?</think>", "", text or "")
    cleaned = cleaned.replace(THINK_OPEN, "").replace(THINK_CLOSE, "")
    return cleaned.strip()


def _match_tag_prefix_length(text: str, tag: str) -> int:
    # 判断 text 末尾有多少字符是 tag 前缀。
    max_len = min(len(text), len(tag) - 1)
    for size in range(max_len, 0, -1):
        if text.endswith(tag[:size]):
            return size
    return 0


def _consume_visible_stream_text(text: str, state: dict[str, Any]) -> str:
    # 兼容两类 MiniMax 流式返回形态：
    # 1. reasoning_content 独立字段
    # 2. content 中夹带 <think>...</think>
    # 这里会过滤隐藏推理，只保留用户可见正文。
    data = f"{state.get('pending_tag', '')}{text}"
    state["pending_tag"] = ""
    visible_parts: list[str] = []
    index = 0

    while index < len(data):
        if state.get("inside_think", False):
            close_index = data.find(THINK_CLOSE, index)
            if close_index == -1:
                partial = _match_tag_prefix_length(data[index:], THINK_CLOSE)
                if partial:
                    state["pending_tag"] = data[-partial:]
                return "".join(visible_parts)
            index = close_index + len(THINK_CLOSE)
            state["inside_think"] = False
            continue

        open_index = data.find(THINK_OPEN, index)
        if open_index == -1:
            remainder = data[index:]
            partial = _match_tag_prefix_length(remainder, THINK_OPEN)
            if partial:
                visible_parts.append(remainder[:-partial])
                state["pending_tag"] = remainder[-partial:]
            else:
                visible_parts.append(remainder)
            return "".join(visible_parts)

        visible_parts.append(data[index:open_index])
        index = open_index + len(THINK_OPEN)
        state["inside_think"] = True

    return "".join(visible_parts)


def _build_direct_analysis(content: str) -> SemanticAnalysis:
    # 当前链路已去掉语义理解 LLM。
    # 这里构造一个默认分析对象，供后续 prompt、摘要和前端展示统一使用。
    return SemanticAnalysis(
        intent="qa",
        is_follow_up=False,
        rewrite_query=content.strip(),
        entities=[],
        needs_retrieval=True,
        risk_level="medium",
        memory_summary="",
    )


def _answer_with_minimax(
    *,
    db: Session,
    session: ChatSession,
    content: str,
):
    # 单次问答主流程：
    # 1. 加载历史消息和会话摘要
    # 2. 检索知识库
    # 3. 组织证据上下文
    # 4. 调用模型生成答案
    # 5. 提取追问建议和会话摘要
    # 读取 prompt 历史消息。
    recent_messages = _load_prompt_messages(db, session.id, content)
    # 构造记忆上下文。
    memory_context = _build_memory_context(session, recent_messages)

    # 直接检索方案：不再调用语义理解模型，直接使用用户原问题做检索。
    analysis: SemanticAnalysis = _build_direct_analysis(content)
    retrieval_query = content.strip()

    # 执行知识库检索。
    retrieval_results = retrieve(db, session.id, retrieval_query, top_k=QA_RETRIEVE_TOP_K)
    # 打包证据文本和结构化引用。
    evidence_context, evidence_items = build_evidence_context(retrieval_results)
    # 改造后，这一步通过 LangChain Runnable 链执行：
    # ChatPromptTemplate -> ChatOpenAI -> StrOutputParser
    answer_text = invoke_answer_chain(
        question=content,
        analysis=analysis,
        memory_summary=memory_context,
        recent_messages=recent_messages,
        evidence_context=evidence_context,
    )
    answer_text = _strip_hidden_reasoning(answer_text)

    # 如果模型没返回正文且证据又弱，则使用兜底答案。
    if not answer_text.strip() and _should_fallback_no_answer(retrieval_results):
        answer_text, legacy_references, suggestions = build_answer(content, retrieval_results)
        summary_text = extract_answer_summary(answer_text)
        session_summary = build_session_summary(
            question=content,
            analysis=analysis,
            answer_text=answer_text,
            retrieval_results=retrieval_results,
        )
        return {
            "answer": answer_text,
            "references": legacy_references,
            "suggestions": suggestions,
            "summary": summary_text,
            "session_summary": session_summary,
        }

    # 提取追问建议与摘要。
    suggestions = extract_followup_questions(answer_text)
    summary_text = extract_answer_summary(answer_text)
    session_summary = build_session_summary(
        question=content,
        analysis=analysis,
        answer_text=answer_text,
        retrieval_results=retrieval_results,
    )
    return {
        "answer": answer_text,
        "references": evidence_items,
        "suggestions": suggestions,
        "summary": summary_text,
        "session_summary": session_summary,
    }


def send_message(db: Session, session: ChatSession, content: str) -> tuple[Message, Message, list[str]]:
    # 非流式发送消息：同步返回完整答案，同时把用户消息和助手消息都落库。
    # 先落库用户消息，保证即使后续模型失败，对话记录也完整。
    user_message = Message(session_id=session.id, role="user", content=content)
    db.add(user_message)
    db.commit()
    db.refresh(user_message)

    try:
        # 调用完整问答链路。
        result = _answer_with_minimax(db=db, session=session, content=content)
    except MinimaxConfigError:
        traceback.print_exc()
        # 如果模型配置缺失，则退化为“检索 + 本地拼接兜底回答”。
        retrieval_results = retrieve(db, session.id, content, top_k=QA_RETRIEVE_TOP_K)
        answer_text, references, suggestions = build_answer(content, retrieval_results)
        result = {
            "answer": answer_text,
            "references": references,
            "suggestions": suggestions,
            "summary": extract_answer_summary(answer_text),
            "session_summary": {
                "topic": "qa",
                "last_user_question": content,
                "last_answer_summary": extract_answer_summary(answer_text),
                "key_entities": [],
                "rewrite_query": content,
                "retrieved_count": len(retrieval_results),
                "confidence": 0.2,
            },
        }

    # 创建助手消息记录。
    assistant_message = Message(
        session_id=session.id,
        role="assistant",
        content=result["answer"],
        references_json=json.dumps(result["references"], ensure_ascii=False),
    )
    db.add(assistant_message)

    # 将本轮问答摘要写回会话。
    session.summary_json = json.dumps(result["session_summary"], ensure_ascii=False)
    # 若标题仍是默认值，则用当前问题前 20 个字符生成标题。
    if not session.title or session.title == "新建会话":
        session.title = content[:20]
    db.add(session)
    db.commit()
    db.refresh(assistant_message)

    return user_message, assistant_message, result["suggestions"]


def stream_message(db: Session, session: ChatSession, content: str) -> StreamingResponse:
    # 流式发送消息：后端通过 SSE 连续推送状态、证据、增量文本和结束事件。
    # 流式接口也先落库用户消息。
    user_message = Message(session_id=session.id, role="user", content=content)
    db.add(user_message)
    db.commit()
    db.refresh(user_message)

    # 提前记录基础类型 id，避免流式生成器里直接持有已过期 ORM 对象。
    session_id = session.id
    user_message_id = user_message.id

    def event_stream():
        # 告知前端流式开始。
        yield _sse("start", {"message_id": user_message_id, "session_id": session_id})
        try:
            # 在生成器内部重新查询会话，避免 DetachedInstanceError。
            session_row = db.query(ChatSession).filter(ChatSession.id == session_id).first()
            if session_row is None:
                yield _sse("error", {"code": "session_not_found", "message": "当前会话不存在，请刷新后重试。"})
                return

            # 读取 prompt 历史和记忆上下文。
            recent_messages = _load_prompt_messages(db, session_id, content)
            memory_context = _build_memory_context(session_row, recent_messages)

            # 当前链路不再调用语义理解模型，直接进入检索阶段。
            yield _sse("status", {"phase": "retrieving", "message": "正在检索知识库"})
            analysis = _build_direct_analysis(content)
            retrieval_query = content.strip()
            retrieval_results = retrieve(db, session_id, retrieval_query, top_k=QA_RETRIEVE_TOP_K)
            evidence_context, evidence_items = build_evidence_context(retrieval_results)

            # 将命中的证据先发给前端，便于用户提前看到来源。
            yield _sse(
                "evidence",
                {
                    "query": retrieval_query,
                    "analysis": {
                        "intent": analysis.intent,
                        "is_follow_up": analysis.is_follow_up,
                        "entities": analysis.entities,
                        "risk_level": analysis.risk_level,
                    },
                    "evidence": evidence_items,
                },
            )

            # 用于收集完整答案，便于结束后落库。
            collected: list[str] = []
            yield _sse("status", {"phase": "generating", "message": "正在整理答案"})

            try:
                # 逐块消费 LangChain Runnable 的流式文本输出，并透传给前端。
                for text in stream_answer_chain(
                    question=content,
                    analysis=analysis,
                    memory_summary=memory_context,
                    recent_messages=recent_messages,
                    evidence_context=evidence_context,
                ):
                    if not text:
                        continue
                    collected.append(text)
                    yield _sse("delta", {"content": text})
            except Exception:
                traceback.print_exc()
                # 如果模型流式报错，且一个正文块都没拿到，并且证据很弱，则退化为兜底回答。
                if not collected and _should_fallback_no_answer(retrieval_results):
                    answer_text, references, suggestions = build_answer(content, retrieval_results)
                    summary_text = extract_answer_summary(answer_text)
                    session_row.summary_json = json.dumps(
                        {
                            "topic": analysis.intent,
                            "last_user_question": content,
                            "last_answer_summary": summary_text,
                            "key_entities": [],
                            "rewrite_query": retrieval_query,
                            "retrieved_count": len(retrieval_results),
                            "confidence": 0.2,
                        },
                        ensure_ascii=False,
                    )
                    assistant_message = Message(
                        session_id=session_id,
                        role="assistant",
                        content=answer_text,
                        references_json=json.dumps(references, ensure_ascii=False),
                    )
                    db.add(assistant_message)
                    if not session_row.title or session_row.title == "新建会话":
                        session_row.title = content[:20]
                    db.add(session_row)
                    db.commit()
                    yield _sse("delta", {"content": answer_text})
                    yield _sse("end", {"answer": answer_text, "summary": summary_text, "suggestions": suggestions})
                    return
                raise

            # 拼接所有流式文本得到完整答案。
            answer_text = _strip_hidden_reasoning("".join(collected).strip())
            if not answer_text and _should_fallback_no_answer(retrieval_results):
                answer_text, references, suggestions = build_answer(content, retrieval_results)
                references_payload = references
            else:
                references_payload = evidence_items
                suggestions = extract_followup_questions(answer_text)

            # 生成摘要并写回会话。
            summary_text = extract_answer_summary(answer_text)
            session_summary = build_session_summary(
                question=content,
                analysis=analysis,
                answer_text=answer_text,
                retrieval_results=retrieval_results,
            )
            session_row.summary_json = json.dumps(session_summary, ensure_ascii=False)

            # 落库助手消息。
            assistant_message = Message(
                session_id=session_id,
                role="assistant",
                content=answer_text,
                references_json=json.dumps(references_payload, ensure_ascii=False),
            )
            db.add(assistant_message)
            if not session_row.title or session_row.title == "新建会话":
                session_row.title = content[:20]
            db.add(session_row)
            db.commit()

            # 通知前端流式结束。
            yield _sse("end", {"answer": answer_text, "summary": summary_text, "suggestions": suggestions})
        except MinimaxConfigError:
            traceback.print_exc()
            # 如果模型配置缺失，则直接退化为本地兜底回答。
            session_row = db.query(ChatSession).filter(ChatSession.id == session_id).first()
            retrieval_results = retrieve(db, session_id, content, top_k=QA_RETRIEVE_TOP_K)
            answer_text, references, suggestions = build_answer(content, retrieval_results)
            summary_text = extract_answer_summary(answer_text)
            if session_row is not None:
                session_row.summary_json = json.dumps(
                    {
                        "topic": "qa",
                        "last_user_question": content,
                        "last_answer_summary": summary_text,
                        "key_entities": [],
                        "rewrite_query": content,
                        "retrieved_count": len(retrieval_results),
                        "confidence": 0.2,
                    },
                    ensure_ascii=False,
                )
            assistant_message = Message(
                session_id=session_id,
                role="assistant",
                content=answer_text,
                references_json=json.dumps(references, ensure_ascii=False),
            )
            db.add(assistant_message)
            if session_row is not None:
                if not session_row.title or session_row.title == "新建会话":
                    session_row.title = content[:20]
                db.add(session_row)
            db.commit()
            yield _sse("delta", {"content": answer_text})
            yield _sse("end", {"answer": answer_text, "summary": summary_text, "suggestions": suggestions})
        except Exception as exc:
            # 统一捕获并回滚事务。
            db.rollback()
            code, message = _friendly_stream_error(exc)
            yield _sse("error", {"code": code, "message": message})

    # 返回标准 SSE 响应。
    # 这里显式关闭缓存和代理缓冲，尽量减少中间层把流式内容攒包。
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: str, payload: dict[str, Any]) -> str:
    # 将事件名和 JSON payload 拼成标准 SSE 文本格式。
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
