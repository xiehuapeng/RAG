from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import SESSION_EXPIRE_DAYS
from app.models import SessionToken, User
from app.security import generate_token, hash_password, verify_password


def bootstrap_admin(db: Session) -> None:
    # Seed a local default admin so a fresh database can be used immediately.
    user = db.query(User).filter(User.username == "admin").first()
    if user:
        return
    db.add(User(username="admin", password_hash=hash_password("admin123"), role="admin"))
    db.commit()


def login(db: Session, username: str, password: str) -> tuple[User | None, str | None]:
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        return None, None

    # Sessions are stored server-side so logout can invalidate tokens immediately.
    token = generate_token()
    session = SessionToken(
        user_id=user.id,
        token=token,
        expires_at=datetime.utcnow() + timedelta(days=SESSION_EXPIRE_DAYS),
    )
    user.last_login = datetime.utcnow()
    db.add(session)
    db.commit()
    db.refresh(user)
    return user, token


def logout(db: Session, token: str | None) -> None:
    if not token:
        return
    session = db.query(SessionToken).filter(SessionToken.token == token).first()
    if session:
        db.delete(session)
        db.commit()
