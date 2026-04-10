from __future__ import annotations

from datetime import datetime

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SessionToken, User


def get_current_user(
    db: Session = Depends(get_db),
    x_session_token: str | None = Header(default=None),
) -> User:
    # 前端把登录成功后拿到的会话 token 放在 x-session-token 头里。
    # 这里统一完成登录态校验并返回当前用户。
    if not x_session_token:
        raise HTTPException(status_code=401, detail={"code": 2003, "message": "session expired"})

    session = db.query(SessionToken).filter(SessionToken.token == x_session_token).first()
    # “不存在”和“已过期”都按 session expired 返回，前端只需要处理一种情况。
    if not session or session.expires_at <= datetime.utcnow():
        raise HTTPException(status_code=401, detail={"code": 2003, "message": "session expired"})
    return session.user


def require_admin(user: User = Depends(get_current_user)) -> User:
    # 管理员接口在已登录的前提下，继续校验角色是否为 admin。
    if user.role != "admin":
        raise HTTPException(status_code=403, detail={"code": 2004, "message": "permission denied"})
    return user
