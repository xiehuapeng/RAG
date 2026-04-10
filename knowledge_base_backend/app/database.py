from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import DATA_DIR, DB_PATH, UPLOAD_DIR


# 在应用首次访问数据库和上传目录前，先保证运行期目录存在。
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    # SQLite 文件由当前进程内多个请求共享，因此要关闭默认线程检查。
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
Base = declarative_base()


def get_db():
    # FastAPI 请求级数据库依赖：请求结束后自动关闭 Session。
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_context():
    # 非 Web 请求场景下的数据库上下文助手，自动提交/回滚。
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def utcnow() -> datetime:
    # 统一封装时间获取，后续如需切换时区策略只改这一处。
    return datetime.utcnow()
