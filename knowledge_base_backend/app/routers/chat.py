from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import ChatSession, Feedback, Message
from app.responses import success
from app.schemas import CreateSessionRequest, FeedbackRequest, SendMessageRequest
from app.services.dashboard import popular_questions
from app.services.chat import create_session as create_session_service
from app.services.chat import delete_session as delete_session_service
from app.services.chat import send_message, stream_message


router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/sessions")
def list_sessions(db: Session = Depends(get_db), user=Depends(get_current_user)):
    # 返回当前用户的所有会话，并附带最后一条消息摘要，方便前端左侧会话栏展示。
    rows = (
        db.query(ChatSession)
        .filter(ChatSession.user_id == user.id)
        .order_by(ChatSession.updated_at.desc())
        .all()
    )
    data = []
    for row in rows:
        last_message = (
            db.query(Message)
            .filter(Message.session_id == row.id)
            .order_by(Message.created_at.desc())
            .first()
        )
        data.append(
            {
                "id": row.id,
                "title": row.title,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "last_message_time": last_message.created_at if last_message else None,
                "last_message_preview": (last_message.content[:80] if last_message else ""),
            }
        )
    return success(data)


@router.post("/sessions/create")
def create_session(payload: CreateSessionRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    # 新建空白会话，真正的问答内容会在发送消息时写入。
    session = create_session_service(db, user.id, payload.title)
    return success({"id": session.id, "title": session.title})


@router.post("/sessions/{session_id}")
def get_session(session_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    # 进入聊天页时，前端通过这个接口恢复整段历史消息。
    session = db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.user_id == user.id).first()
    if not session:
        return {"code": 1000, "message": "session not found", "data": None}
    messages = db.query(Message).filter(Message.session_id == session_id).order_by(Message.created_at.asc()).all()
    return success(
            {
                "id": session.id,
                "title": session.title,
                "summary": json.loads(session.summary_json) if session.summary_json else {},
                "messages": [
                    {
                        "id": message.id,
                    "role": message.role,
                    "content": message.content,
                    "references": json.loads(message.references_json) if message.references_json else [],
                    "created_at": message.created_at,
                }
                for message in messages
            ],
        }
    )


@router.post("/sessions/{session_id}/delete")
def delete_session(session_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    # 删除会话时，相关消息、反馈和检索日志也会一起清理。
    session = db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.user_id == user.id).first()
    if not session:
        return {"code": 1000, "message": "session not found", "data": None}
    delete_session_service(db, session)
    return success(message="session deleted")


@router.post("/sessions/{session_id}/messages")
def post_message(
    session_id: int,
    payload: SendMessageRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    # 查询会话，验证会话存在且属于当前用户
    session = db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.user_id == user.id).first()
    if not session:
        return {"code": 1000, "message": "session not found", "data": None}
    # 发送消息并获取助手回复和建议问题
    _, assistant_message, suggestions = send_message(db, session, payload.content)
    return success(
        {
            "message_id": assistant_message.id,
            "content": assistant_message.content,
            "references": json.loads(assistant_message.references_json) if assistant_message.references_json else [],
            "related_chunks": json.loads(assistant_message.references_json) if assistant_message.references_json else [],
            "suggested_questions": suggestions,
        }
    )


@router.post("/sessions/{session_id}/messages/stream")
def post_message_stream(
    session_id: int,
    payload: SendMessageRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    session = db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.user_id == user.id).first()
    if not session:
        return {"code": 1000, "message": "session not found", "data": None}
    return stream_message(db, session, payload.content)


@router.post("/popular-questions")
def get_popular_questions(db: Session = Depends(get_db), user=Depends(get_current_user)):
    # 首页/聊天页的推荐问题列表。
    return success(popular_questions(db))


@router.post("/messages/{message_id}/feedback")
def send_feedback(
    message_id: int,
    payload: FeedbackRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    # 用户对回答做正负反馈，后续可用于运营分析或效果评估。
    if payload.feedback not in {"positive", "negative"}:
        return {"code": 1000, "message": "invalid feedback", "data": None}
    message = db.query(Message).filter(Message.id == message_id).first()
    if not message:
        return {"code": 1000, "message": "message not found", "data": None}
    item = Feedback(message_id=message_id, feedback=payload.feedback, comment=payload.comment)
    db.add(item)
    db.commit()
    return success(message="feedback saved")
