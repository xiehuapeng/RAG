from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import APP_NAME
from app.database import Base, SessionLocal, engine
from app.responses import error, success
from app.routers import auth, chat, config, dashboard, documents
from app.services.auth import bootstrap_admin
from app.services.documents_langchain import backfill_vector_store
from app.services.migrations import run_startup_migrations


# 后端应用入口：
# 负责装配 API、初始化运行环境，并在生产环境托管前端静态资源。
app = FastAPI(title=APP_NAME, version="1.0.0")
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
STATIC_INDEX = STATIC_DIR / "index.html"

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 所有业务路由统一在这里注册，便于快速看清系统能力边界。
app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(dashboard.router)
app.include_router(config.router)

if STATIC_DIR.exists():
    # 生产构建后的前端资源统一位于 static/assets 下。
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


@app.on_event("startup")
def on_startup() -> None:
    # 启动阶段保证数据库结构、管理员账号和历史索引状态都可用。
    Base.metadata.create_all(bind=engine)
    run_startup_migrations(engine)
    db = SessionLocal()
    try:
        bootstrap_admin(db)
        backfill_vector_store(db)
    finally:
        db.close()


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # 项目统一错误结构，便于前端只维护一套错误处理逻辑。
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content={**exc.detail, "data": None})
    return JSONResponse(status_code=exc.status_code, content=error(1000, str(exc.detail)))


@app.get("/health")
def health():
    # 供部署平台和监控系统探活使用。
    return success({"status": "ok"})


@app.get("/")
def frontend_index():
    # 根路径默认返回前端入口页。
    if STATIC_INDEX.exists():
        return FileResponse(STATIC_INDEX)
    return JSONResponse(status_code=404, content=error(1000, "frontend not built"))


@app.get("/{full_path:path}")
def frontend_routes(full_path: str):
    # SPA 路由兜底：除了 API 和健康检查，其它路径全部交给前端路由处理。
    if full_path.startswith("api/") or full_path == "health":
        return JSONResponse(status_code=404, content=error(1000, "not found"))
    if STATIC_INDEX.exists():
        return FileResponse(STATIC_INDEX)
    return JSONResponse(status_code=404, content=error(1000, "frontend not built"))
