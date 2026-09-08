from __future__ import annotations

import json
import logging
import re
import traceback
from typing import Any

import httpx
from fastapi.responses import StreamingResponse
from openai import AuthenticationError as OpenAIAuthenticationError
from openai import OpenAIError
from sqlalchemy.orm import Session

from app.config import QA_RETRIEVE_TOP_K
from app.models import ChatSession, Feedback, Message, QueryUnderstandingLog, RetrievalLog
from app.qa_logging import QuestionLogSession, emit_qa_log
from app.schemas import QueryUnderstandingResult
from app.services.llm_client import LLMConfigError
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
from app.services.query_understanding import get_query_understanding_service
from app.services.retrieval_langchain import retrieve, retrieve_with_understanding


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

NO_ANSWER_TEXT = (
    "当前知识库里没有找到足够可靠的内容来回答这个问题。"
    "你可以换一种问法，或者补充更具体的场景、设备、流程或文档名称。"
)
OUT_OF_SCOPE_TEXT = (
    "这个问题看起来不在当前知识库覆盖范围内。"
    "目前系统主要回答企业业务、开户、计费、物联网卡、CMIOT、群组、成员、缴费等知识库资料相关问题。"
)

THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"

PROGRESS_STEPS = [
    ("start", "开始", "开始处理问题"),
    ("understanding", "理解问题", "正在理解问题"),
    ("retrieving", "检索知识库", "正在检索知识库"),
    ("filtering", "分片过滤", "正在分片过滤"),
    ("generating", "整理答案", "正在整理答案"),
    ("completed", "完成", "处理完成"),
]
PROGRESS_STEP_MESSAGES = {key: message for key, _, message in PROGRESS_STEPS}
PROGRESS_CHAIN_KEYS = {"retrieving", "filtering", "generating"}


def _serialize_model(model: QueryUnderstandingResult | None) -> dict[str, Any]:
    if model is None:
        return {}
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _log_understanding_summary(source_question: str, understanding: QueryUnderstandingResult) -> None:
    rewritten_question = understanding.rewrite_query or understanding.raw_query or source_question
    emit_qa_log(
        "理解问题",
        f"源问题={source_question} | 改写后问题={rewritten_question}",
    )


def _run_query_understanding(
    db: Session,
    *,
    session: ChatSession,
    content: str,
    recent_messages: list[dict[str, Any]],
    session_summary: dict[str, Any],
) -> tuple[QueryUnderstandingResult, SemanticAnalysis]:
    understanding_service = get_query_understanding_service()
    understanding = understanding_service.understand(
        query=content,
        history=recent_messages,
        session_summary=session_summary,
    )
    _log_understanding_summary(content, understanding)
    _record_query_understanding_log(
        db,
        session_id=session.id,
        user_id=session.user_id,
        understanding=understanding,
    )
    analysis = _analysis_from_understanding(understanding)
    return understanding, analysis


def _friendly_stream_error(exc: Exception) -> tuple[str, str]:
    message = str(exc).lower()
    if isinstance(exc, OpenAIAuthenticationError):
        return "config_error", "模型服务鉴权失败，请检查 API Key、Base URL 或模型配置。"
    if (
        "api secret key" in message
        or "authorized_error" in message
        or "invalid api key" in message
        or "authenticationerror" in message
    ):
        return "config_error", "模型服务鉴权失败，请检查 API Key、Base URL 或模型配置。"
    if isinstance(exc, LLMConfigError):
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


def _default_query_understanding(content: str) -> QueryUnderstandingResult:
    query = (content or "").strip()
    return QueryUnderstandingResult(
        route="kb_qa",
        confidence=0.2,
        is_follow_up=False,
        need_context=False,
        raw_query=query,
        rewrite_query=query,
        keywords_hit=[],
        entities=[],
        search_terms=[],
        search_queries=[],
        filters={},
        reason="default_direct_query",
        provider="rules",
        model="rules",
        fallback_used=False,
    )


def _analysis_from_understanding(result: QueryUnderstandingResult) -> SemanticAnalysis:
    return SemanticAnalysis(
        intent="qa" if result.route == "kb_qa" else "out_of_scope",
        route=result.route,
        raw_query=result.raw_query,
        is_follow_up=result.is_follow_up,
        rewrite_query=result.rewrite_query or result.raw_query,
        keywords_hit=result.keywords_hit,
        entities=result.entities,
        filters=result.filters,
        needs_retrieval=result.route == "kb_qa",
        risk_level="medium",
        reason=result.reason,
        confidence=result.confidence,
    )


def _record_query_understanding_log(
    db: Session,
    *,
    session_id: int,
    user_id: int | None,
    understanding: QueryUnderstandingResult,
) -> None:
    db.add(
        QueryUnderstandingLog(
            session_id=session_id,
            user_id=user_id,
            raw_query=understanding.raw_query,
            rewrite_query=understanding.rewrite_query,
            route=understanding.route,
            confidence=understanding.confidence,
            is_follow_up=1 if understanding.is_follow_up else 0,
            need_context=1 if understanding.need_context else 0,
            keywords_hit_json=json.dumps(understanding.keywords_hit, ensure_ascii=False),
            entities_json=json.dumps(understanding.entities, ensure_ascii=False),
            search_terms_json=json.dumps(understanding.search_terms, ensure_ascii=False),
            search_queries_json=json.dumps(understanding.search_queries, ensure_ascii=False),
            filters_json=json.dumps(understanding.filters, ensure_ascii=False),
            reason=understanding.reason,
            provider=understanding.provider,
            model=understanding.model,
            fallback_used=1 if understanding.fallback_used else 0,
        )
    )
    db.commit()


def _should_fallback_no_answer(retrieval_results: list[dict]) -> bool:
    if not retrieval_results:
        return True

    top = retrieval_results[0]
    top_score = float(top.get("score") or 0.0)
    top_keyword = float(top.get("keyword_score") or 0.0)
    top_vector = float(top.get("vector_score") or 0.0)
    has_keyword_hit = any(float(item.get("keyword_score") or 0.0) >= 0.08 for item in retrieval_results[:3])
    has_title_hit = any(bool(item.get("title_hit")) for item in retrieval_results[:3])

    if top_score < 0.2:
        return True
    if not has_keyword_hit and not has_title_hit and top_vector < 0.55:
        return True
    if top_score < 0.3 and top_keyword < 0.05:
        return True
    return False


def build_answer(query: str, retrieval_results: list[dict]) -> tuple[str, list[dict], list[str]]:
    del query
    if _should_fallback_no_answer(retrieval_results):
        return (
            NO_ANSWER_TEXT,
            [],
            ["这个问题对应的流程在哪里", "有没有更具体的操作条件", "是否有补充材料或上下文"],
        )

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
    session = ChatSession(user_id=user_id, title=title or "新建会话")
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def delete_session(db: Session, session: ChatSession) -> None:
    message_ids = [message_id for (message_id,) in db.query(Message.id).filter(Message.session_id == session.id).all()]
    if message_ids:
        db.query(Feedback).filter(Feedback.message_id.in_(message_ids)).delete(synchronize_session=False)

    db.query(Message).filter(Message.session_id == session.id).delete(synchronize_session=False)
    db.query(RetrievalLog).filter(RetrievalLog.session_id == session.id).delete(synchronize_session=False)
    db.query(QueryUnderstandingLog).filter(QueryUnderstandingLog.session_id == session.id).delete(
        synchronize_session=False
    )
    db.delete(session)
    db.commit()


def _build_memory_context(session_summary: dict[str, Any], recent_messages: list[dict[str, Any]]) -> str:
    return build_memory_summary(session_summary, recent_messages)


def _load_prompt_messages(db: Session, session_id: int, current_content: str) -> list[dict[str, Any]]:
    recent_messages = load_history_messages(db, session_id)
    if recent_messages and recent_messages[-1].get("role") == "user":
        last_content = str(recent_messages[-1].get("content") or "").strip()
        if last_content == current_content.strip():
            return recent_messages[:-1]
    return recent_messages


def _strip_hidden_reasoning(text: str) -> str:
    cleaned = re.sub(r"(?is)<think>.*?</think>", "", text or "")
    cleaned = cleaned.replace(THINK_OPEN, "").replace(THINK_CLOSE, "")
    return cleaned.strip()


def _is_model_runtime_error(exc: Exception) -> bool:
    return isinstance(exc, (LLMConfigError, OpenAIError, httpx.HTTPError, TimeoutError))


def _build_progress_payload(
    current_step: str,
    *,
    message: str | None = None,
    skipped_steps: set[str] | None = None,
) -> dict[str, Any]:
    skipped = skipped_steps or set()
    current_index = next((index for index, (key, _, _) in enumerate(PROGRESS_STEPS) if key == current_step), 0)
    steps: list[dict[str, str]] = []

    for index, (key, label, _) in enumerate(PROGRESS_STEPS):
        if key in skipped:
            status = "skipped"
        elif current_step == "completed":
            status = "done" if index <= current_index else "waiting"
        elif key == current_step:
            status = "active"
        elif index < current_index:
            status = "done"
        else:
            status = "waiting"
        steps.append({"key": key, "label": label, "status": status})

    return {
        "current_step": current_step,
        "message": message or PROGRESS_STEP_MESSAGES.get(current_step, "正在处理中"),
        "steps": steps,
    }


def _build_local_fallback_result(
    *,
    content: str,
    retrieval_results: list[dict],
    analysis: SemanticAnalysis,
    understanding: QueryUnderstandingResult,
) -> dict[str, Any]:
    answer_text, references, suggestions = build_answer(content, retrieval_results)
    summary_text = extract_answer_summary(answer_text)
    query_understanding = _serialize_model(understanding)
    return {
        "answer": answer_text,
        "references": references,
        "suggestions": suggestions,
        "summary": summary_text,
        "session_summary": build_session_summary(
            question=content,
            analysis=analysis,
            answer_text=answer_text,
            retrieval_results=retrieval_results,
            query_understanding=query_understanding,
        ),
        "query_understanding": query_understanding,
    }


def _build_out_of_scope_result(content: str, understanding: QueryUnderstandingResult) -> dict[str, Any]:
    analysis = _analysis_from_understanding(understanding)
    answer_text = OUT_OF_SCOPE_TEXT
    suggestions = ["换一个更具体的业务问题", "补充文档名称、业务名称或办理场景", "改问知识库中的流程或规则"]
    summary_text = extract_answer_summary(answer_text)
    query_understanding = _serialize_model(understanding)
    return {
        "answer": answer_text,
        "references": [],
        "suggestions": suggestions,
        "summary": summary_text,
        "session_summary": build_session_summary(
            question=content,
            analysis=analysis,
            answer_text=answer_text,
            retrieval_results=[],
            query_understanding=query_understanding,
        ),
        "query_understanding": query_understanding,
    }


def _prepare_turn_context(db: Session, session: ChatSession, content: str) -> dict[str, Any]:
    recent_messages = _load_prompt_messages(db, session.id, content)
    session_summary = parse_session_summary(session.summary_json)
    memory_context = _build_memory_context(session_summary, recent_messages)

    understanding, analysis = _run_query_understanding(
        db,
        session=session,
        content=content,
        recent_messages=recent_messages,
        session_summary=session_summary,
    )
    retrieval_results: list[dict] = []
    evidence_context = ""
    evidence_items: list[dict[str, Any]] = []
    retrieval_query = understanding.rewrite_query or understanding.raw_query

    if understanding.route == "kb_qa":
        retrieval_results = retrieve_with_understanding(db, session.id, understanding, top_k=QA_RETRIEVE_TOP_K)
        evidence_context, evidence_items = build_evidence_context(retrieval_results)

    return {
        "recent_messages": recent_messages,
        "session_summary": session_summary,
        "memory_context": memory_context,
        "understanding": understanding,
        "analysis": analysis,
        "retrieval_query": retrieval_query,
        "retrieval_results": retrieval_results,
        "evidence_context": evidence_context,
        "evidence_items": evidence_items,
    }


def _answer_with_llm(
    *,
    db: Session,
    session: ChatSession,
    content: str,
):
    context = _prepare_turn_context(db, session, content)
    understanding: QueryUnderstandingResult = context["understanding"]
    analysis: SemanticAnalysis = context["analysis"]

    if understanding.route != "kb_qa":
        return _build_out_of_scope_result(content, understanding)

    retrieval_results = context["retrieval_results"]
    evidence_context = context["evidence_context"]
    evidence_items = context["evidence_items"]
    memory_context = context["memory_context"]
    recent_messages = context["recent_messages"]

    try:
        answer_text = invoke_answer_chain(
            question=content,
            analysis=analysis,
            memory_summary=memory_context,
            recent_messages=recent_messages,
            evidence_context=evidence_context,
        )
    except Exception as exc:
        if not _is_model_runtime_error(exc):
            raise
        traceback.print_exc()
        return _build_local_fallback_result(
            content=content,
            retrieval_results=retrieval_results,
            analysis=analysis,
            understanding=understanding,
        )

    answer_text = _strip_hidden_reasoning(answer_text)
    if not answer_text.strip() and _should_fallback_no_answer(retrieval_results):
        answer_text, legacy_references, suggestions = build_answer(content, retrieval_results)
        summary_text = extract_answer_summary(answer_text)
        query_understanding = _serialize_model(understanding)
        session_summary = build_session_summary(
            question=content,
            analysis=analysis,
            answer_text=answer_text,
            retrieval_results=retrieval_results,
            query_understanding=query_understanding,
        )
        return {
            "answer": answer_text,
            "references": legacy_references,
            "suggestions": suggestions,
            "summary": summary_text,
            "session_summary": session_summary,
            "query_understanding": query_understanding,
        }

    suggestions = extract_followup_questions(answer_text)
    summary_text = extract_answer_summary(answer_text)
    query_understanding = _serialize_model(understanding)
    session_summary = build_session_summary(
        question=content,
        analysis=analysis,
        answer_text=answer_text,
        retrieval_results=retrieval_results,
        query_understanding=query_understanding,
    )
    return {
        "answer": answer_text,
        "references": evidence_items,
        "suggestions": suggestions,
        "summary": summary_text,
        "session_summary": session_summary,
        "query_understanding": query_understanding,
    }


def send_message(db: Session, session: ChatSession, content: str) -> tuple[Message, Message, list[str]]:
    user_message = Message(session_id=session.id, role="user", content=content)
    db.add(user_message)
    db.commit()
    db.refresh(user_message)

    with QuestionLogSession(content):
        try:
            result = _answer_with_llm(db=db, session=session, content=content)
        except Exception as exc:
            if not _is_model_runtime_error(exc):
                raise
            traceback.print_exc()
            understanding = _default_query_understanding(content)
            analysis = _analysis_from_understanding(understanding)
            retrieval_results = retrieve(db, session.id, content, top_k=QA_RETRIEVE_TOP_K)
            result = _build_local_fallback_result(
                content=content,
                retrieval_results=retrieval_results,
                analysis=analysis,
                understanding=understanding,
            )

    assistant_message = Message(
        session_id=session.id,
        role="assistant",
        content=result["answer"],
        references_json=json.dumps(result["references"], ensure_ascii=False),
    )
    db.add(assistant_message)

    session.summary_json = json.dumps(result["session_summary"], ensure_ascii=False)
    if not session.title or session.title == "新建会话":
        session.title = content[:20]
    db.add(session)
    db.commit()
    db.refresh(assistant_message)

    return user_message, assistant_message, result["suggestions"]


def stream_message(db: Session, session: ChatSession, content: str) -> StreamingResponse:
    user_message = Message(session_id=session.id, role="user", content=content)
    db.add(user_message)
    db.commit()
    db.refresh(user_message)

    session_id = session.id
    user_message_id = user_message.id

    def event_stream():
        yield _sse("start", {"message_id": user_message_id, "session_id": session_id})
        with QuestionLogSession(content):
            try:
                session_row = db.query(ChatSession).filter(ChatSession.id == session_id).first()
                if session_row is None:
                    yield _sse("error", {"code": "session_not_found", "message": "当前会话不存在，请刷新后重试。"})
                    return

                yield _sse("progress", _build_progress_payload("start"))

                recent_messages = _load_prompt_messages(db, session_row.id, content)
                session_summary = parse_session_summary(session_row.summary_json)
                memory_context = _build_memory_context(session_summary, recent_messages)

                yield _sse("progress", _build_progress_payload("understanding"))
                understanding, analysis = _run_query_understanding(
                    db,
                    session=session_row,
                    content=content,
                    recent_messages=recent_messages,
                    session_summary=session_summary,
                )

                yield _sse(
                    "understanding",
                    {
                        "route": understanding.route,
                        "confidence": understanding.confidence,
                        "is_follow_up": understanding.is_follow_up,
                        "need_context": understanding.need_context,
                        "raw_query": understanding.raw_query,
                        "rewrite_query": understanding.rewrite_query,
                        "keywords_hit": understanding.keywords_hit,
                        "entities": understanding.entities,
                    },
                )

                if understanding.route != "kb_qa":
                    result = _build_out_of_scope_result(content, understanding)
                    session_row.summary_json = json.dumps(result["session_summary"], ensure_ascii=False)
                    assistant_message = Message(
                        session_id=session_id,
                        role="assistant",
                        content=result["answer"],
                        references_json=json.dumps(result["references"], ensure_ascii=False),
                    )
                    db.add(assistant_message)
                    if not session_row.title or session_row.title == "新建会话":
                        session_row.title = content[:20]
                    db.add(session_row)
                    db.commit()
                    yield _sse("progress", _build_progress_payload("completed", skipped_steps=PROGRESS_CHAIN_KEYS))
                    yield _sse("delta", {"content": result["answer"]})
                    yield _sse(
                        "end",
                        {
                            "answer": result["answer"],
                            "summary": result["summary"],
                            "suggestions": result["suggestions"],
                        },
                    )
                    return

                yield _sse("progress", _build_progress_payload("retrieving"))
                yield _sse("status", {"phase": "retrieving", "message": "正在检索知识库"})
                retrieval_results = retrieve_with_understanding(db, session_row.id, understanding, top_k=QA_RETRIEVE_TOP_K)
                yield _sse("progress", _build_progress_payload("filtering"))
                evidence_context, evidence_items = build_evidence_context(retrieval_results)
                yield _sse(
                    "evidence",
                    {
                        "query": understanding.rewrite_query or understanding.raw_query,
                        "analysis": {
                            "route": analysis.route,
                            "intent": analysis.intent,
                            "is_follow_up": analysis.is_follow_up,
                            "keywords_hit": analysis.keywords_hit,
                            "entities": analysis.entities,
                            "reason": analysis.reason,
                            "confidence": analysis.confidence,
                        },
                        "evidence": evidence_items,
                    },
                )

                collected: list[str] = []
                yield _sse("progress", _build_progress_payload("generating"))
                yield _sse("status", {"phase": "generating", "message": "正在整理答案"})
                try:
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
                except Exception as exc:
                    logger.exception("stream answer failed")
                    if _is_model_runtime_error(exc) and not collected:
                        fallback_result = _build_local_fallback_result(
                            content=content,
                            retrieval_results=retrieval_results,
                            analysis=analysis,
                            understanding=understanding,
                        )
                        session_row.summary_json = json.dumps(fallback_result["session_summary"], ensure_ascii=False)
                        assistant_message = Message(
                            session_id=session_id,
                            role="assistant",
                            content=fallback_result["answer"],
                            references_json=json.dumps(fallback_result["references"], ensure_ascii=False),
                        )
                        db.add(assistant_message)
                        if not session_row.title or session_row.title == "新建会话":
                            session_row.title = content[:20]
                        db.add(session_row)
                        db.commit()
                        yield _sse("progress", _build_progress_payload("completed"))
                        yield _sse("delta", {"content": fallback_result["answer"]})
                        yield _sse(
                            "end",
                            {
                                "answer": fallback_result["answer"],
                                "summary": fallback_result["summary"],
                                "suggestions": fallback_result["suggestions"],
                            },
                        )
                        return
                    if not collected and _should_fallback_no_answer(retrieval_results):
                        fallback_result = _build_local_fallback_result(
                            content=content,
                            retrieval_results=retrieval_results,
                            analysis=analysis,
                            understanding=understanding,
                        )
                        session_row.summary_json = json.dumps(fallback_result["session_summary"], ensure_ascii=False)
                        assistant_message = Message(
                            session_id=session_id,
                            role="assistant",
                            content=fallback_result["answer"],
                            references_json=json.dumps(fallback_result["references"], ensure_ascii=False),
                        )
                        db.add(assistant_message)
                        if not session_row.title or session_row.title == "新建会话":
                            session_row.title = content[:20]
                        db.add(session_row)
                        db.commit()
                        yield _sse("progress", _build_progress_payload("completed"))
                        yield _sse("delta", {"content": fallback_result["answer"]})
                        yield _sse(
                            "end",
                            {
                                "answer": fallback_result["answer"],
                                "summary": fallback_result["summary"],
                                "suggestions": fallback_result["suggestions"],
                            },
                        )
                        return
                    raise

                answer_text = _strip_hidden_reasoning("".join(collected).strip())
                if not answer_text and _should_fallback_no_answer(retrieval_results):
                    answer_text, references, suggestions = build_answer(content, retrieval_results)
                    references_payload = references
                else:
                    references_payload = evidence_items
                    suggestions = extract_followup_questions(answer_text)

                summary_text = extract_answer_summary(answer_text)
                query_understanding = _serialize_model(understanding)
                session_summary = build_session_summary(
                    question=content,
                    analysis=analysis,
                    answer_text=answer_text,
                    retrieval_results=retrieval_results,
                    query_understanding=query_understanding,
                )
                session_row.summary_json = json.dumps(session_summary, ensure_ascii=False)

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
                yield _sse("progress", _build_progress_payload("completed"))
                yield _sse("end", {"answer": answer_text, "summary": summary_text, "suggestions": suggestions})
            except Exception as exc:
                db.rollback()
                code, message = _friendly_stream_error(exc)
                yield _sse("error", {"code": code, "message": message})

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
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
