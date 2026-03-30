"""
ScholarAgent 后端入口

FastAPI 应用，注册所有路由，初始化 Skill 和数据库连接。
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import get_settings
from backend.skills.registry import init_skills

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动时初始化，关闭时清理"""

    # ---- Startup ----
    logger.info("Starting ScholarAgent backend...")

    # 初始化数据库表（开发环境，生产环境用 Alembic）
    if settings.app_env == "development":
        from backend.db.postgres import init_db
        await init_db()
        logger.info("Database tables initialized.")

    # 初始化并注册所有 Skill
    init_skills()

    logger.info("ScholarAgent backend is ready.")
    yield

    # ---- Shutdown ----
    logger.info("Shutting down ScholarAgent backend...")


# ========================
# 创建 FastAPI 应用
# ========================
app = FastAPI(
    title="ScholarAgent",
    description="科研知识管理与 AI 辅助研究系统 - 知识后端 API",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS（开发阶段允许所有来源）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========================
# 注册路由
# ========================
from backend.api import documents, proxy, admin

app.include_router(documents.router, prefix=settings.api_prefix)
app.include_router(proxy.router, prefix=settings.api_prefix)
app.include_router(admin.router, prefix=settings.api_prefix)

# Phase 2 routes (uncomment when implemented)
# from backend.api import notes, tasks
# app.include_router(notes.router, prefix=settings.api_prefix)
# app.include_router(tasks.router, prefix=settings.api_prefix)


@app.get("/")
async def root():
    return {
        "name": "ScholarAgent",
        "version": "0.1.0",
        "docs": "/docs",
        "description": "AI 无关的科研知识后端",
    }
