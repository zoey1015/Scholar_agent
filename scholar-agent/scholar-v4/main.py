"""
ScholarAgent 后端入口 v4

新增：
- 深度研究路由（LangGraph Plan-Execute-Replan）
- 数据库迁移（paper_claims, paper_relations, research_state, notifications）
- 上传管线集成（论文上传后触发异步分析）
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
    logger.info("Starting ScholarAgent v4 backend...")

    if settings.app_env == "development":
        from backend.db.postgres import init_db
        await init_db()
        logger.info("Base database tables initialized.")

    # 新增表迁移
    try:
        from backend.db.migrations import run_migrations
        run_migrations(None)
        logger.info("V4 migrations complete.")
    except Exception as e:
        logger.warning(f"V4 migration warning: {e}")

    init_skills()
    logger.info("ScholarAgent v4 backend is ready.")
    yield
    logger.info("Shutting down...")


app = FastAPI(
    title="ScholarAgent",
    description="科研知识管理与 AI 辅助研究系统 · Plan-Execute-Replan 架构",
    version="0.5.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 基础路由
from backend.api import documents, proxy, admin, notes

app.include_router(documents.router, prefix=settings.api_prefix)
app.include_router(proxy.router, prefix=settings.api_prefix)
app.include_router(admin.router, prefix=settings.api_prefix)
app.include_router(notes.router, prefix=settings.api_prefix)

# 深度研究路由（LangGraph）
try:
    from backend.api.research import router as research_router
    app.include_router(research_router, prefix=settings.api_prefix)
    logger.info("Research router loaded (LangGraph + SSE streaming)")
except ImportError as e:
    logger.warning(f"Research router not loaded: {e}. Run: pip install langgraph")


@app.get("/")
async def root():
    return {
        "name": "ScholarAgent",
        "version": "0.5.0",
        "features": [
            "RAG with streaming",
            "LangGraph Plan-Execute-Replan",
            "Cross-paper relation detection (pre-computed)",
            "Research state tracking",
            "Smart notifications",
        ],
    }
