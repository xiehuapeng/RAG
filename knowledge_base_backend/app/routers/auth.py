from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.responses import success
from app.schemas import LoginRequest
from app.services.auth import login as login_service
from app.services.auth import logout as logout_service


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user, token = login_service(db, payload.username, payload.password)
    if not user or not token:
        return {"code": 2001, "message": "username or password invalid", "data": None}
    return success(
        {
            "user_id": user.id,
            "username": user.username,
            "role": user.role,
            "token": token,
        }
    )


@router.post("/logout")
def logout(db: Session = Depends(get_db), x_session_token: str | None = Header(default=None)):
    logout_service(db, x_session_token)
    return success(message="logged out")


@router.post("/me")
def me(user=Depends(get_current_user)):
    return success({"user_id": user.id, "username": user.username, "role": user.role})
