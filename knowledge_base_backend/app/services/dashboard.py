from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Chunk, Document, Feedback, Message, RetrievalLog


def _date_series(days: int) -> list[str]:
    today = datetime.utcnow().date()
    return [(today - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]


def basic_stats(db: Session) -> dict:
    document_count = db.query(func.count(Document.id)).scalar() or 0
    ready_count = db.query(func.count(Document.id)).filter(Document.status == "ready").scalar() or 0
    chunk_count = db.query(func.count(Chunk.id)).scalar() or 0
    retrieval_count = db.query(func.count(RetrievalLog.id)).scalar() or 0
    upload_count = document_count
    parse_success_rate = round(ready_count / document_count, 4) if document_count else 0

    positive = db.query(func.count(Feedback.id)).filter(Feedback.feedback == "positive").scalar() or 0
    negative = db.query(func.count(Feedback.id)).filter(Feedback.feedback == "negative").scalar() or 0
    feedback_total = positive + negative
    positive_rate = round(positive / feedback_total, 4) if feedback_total else 0

    return {
        "document_count": document_count,
        "ready_document_count": ready_count,
        "chunk_count": chunk_count,
        "retrieval_count": retrieval_count,
        "upload_count": upload_count,
        "parse_success_rate": parse_success_rate,
        "feedback_positive_rate": positive_rate,
    }


def ask_trend(db: Session, days: int) -> list[dict]:
    date_labels = _date_series(days)
    rows = (
        db.query(func.date(RetrievalLog.created_at).label("dt"), func.count(RetrievalLog.id))
        .filter(RetrievalLog.created_at >= datetime.utcnow() - timedelta(days=days - 1))
        .group_by(func.date(RetrievalLog.created_at))
        .all()
    )
    row_map = {str(dt): count for dt, count in rows}
    return [{"date": label, "count": row_map.get(label, 0)} for label in date_labels]


def upload_trend(db: Session, days: int) -> list[dict]:
    date_labels = _date_series(days)
    rows = (
        db.query(func.date(Document.created_at).label("dt"), func.count(Document.id))
        .filter(Document.created_at >= datetime.utcnow() - timedelta(days=days - 1))
        .group_by(func.date(Document.created_at))
        .all()
    )
    row_map = {str(dt): count for dt, count in rows}
    return [{"date": label, "count": row_map.get(label, 0)} for label in date_labels]


def top_questions(db: Session) -> list[dict]:
    rows = (
        db.query(RetrievalLog.query, func.count(RetrievalLog.id).label("times"))
        .group_by(RetrievalLog.query)
        .order_by(func.count(RetrievalLog.id).desc(), RetrievalLog.query.asc())
        .limit(20)
        .all()
    )
    return [{"query": query, "times": times} for query, times in rows]


def popular_questions(db: Session) -> list[str]:
    return [item["query"] for item in top_questions(db)[:10]]


def no_answer_questions(db: Session) -> list[dict]:
    rows = (
        db.query(RetrievalLog.query, func.count(RetrievalLog.id).label("times"))
        .filter(RetrievalLog.reranked_chunks == "[]")
        .group_by(RetrievalLog.query)
        .order_by(func.count(RetrievalLog.id).desc(), RetrievalLog.query.asc())
        .limit(20)
        .all()
    )
    return [{"query": query, "times": times} for query, times in rows]


def feedback_stats(db: Session) -> dict:
    positive = db.query(func.count(Feedback.id)).filter(Feedback.feedback == "positive").scalar() or 0
    negative = db.query(func.count(Feedback.id)).filter(Feedback.feedback == "negative").scalar() or 0
    total = positive + negative
    return {
        "positive": positive,
        "negative": negative,
        "total": total,
        "positive_rate": round(positive / total, 4) if total else 0,
    }


def recent_feedbacks(db: Session, limit: int = 20) -> list[dict]:
    rows = (
        db.query(Feedback, Message)
        .join(Message, Message.id == Feedback.message_id)
        .order_by(Feedback.created_at.desc())
        .limit(limit)
        .all()
    )
    data: list[dict] = []
    for feedback, message in rows:
        user_question = (
            db.query(Message.content)
            .filter(
                Message.session_id == message.session_id,
                Message.role == "user",
                Message.created_at <= message.created_at,
            )
            .order_by(Message.created_at.desc())
            .first()
        )
        data.append(
            {
                "created_at": feedback.created_at,
                "feedback": feedback.feedback,
                "comment": feedback.comment,
                "answer": message.content,
                "question": user_question[0] if user_question else "",
            }
        )
    return data
