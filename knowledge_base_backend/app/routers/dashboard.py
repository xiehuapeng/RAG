from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.responses import success
from app.schemas import TrendRequest
from app.services.dashboard import (
    ask_trend,
    basic_stats,
    feedback_stats,
    no_answer_questions,
    recent_feedbacks,
    top_questions,
    upload_trend,
)


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.post("/stats")
def stats(db: Session = Depends(get_db), user=Depends(require_admin)):
    return success(basic_stats(db))


@router.post("/top-questions")
def top_questions_api(db: Session = Depends(get_db), user=Depends(require_admin)):
    return success(top_questions(db))


@router.post("/no-answer")
def no_answer_api(db: Session = Depends(get_db), user=Depends(require_admin)):
    return success(no_answer_questions(db))


@router.post("/feedback")
def feedback_api(db: Session = Depends(get_db), user=Depends(require_admin)):
    return success(feedback_stats(db))


@router.post("/ask-trend")
def ask_trend_api(payload: TrendRequest, db: Session = Depends(get_db), user=Depends(require_admin)):
    return success(ask_trend(db, payload.days))


@router.post("/upload-trend")
def upload_trend_api(payload: TrendRequest, db: Session = Depends(get_db), user=Depends(require_admin)):
    return success(upload_trend(db, payload.days))


@router.post("/recent-feedback")
def recent_feedback_api(db: Session = Depends(get_db), user=Depends(require_admin)):
    return success(recent_feedbacks(db))
