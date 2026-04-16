"""
ScholarAgent 后端入口（Phase 2）
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
    logger.info("Starting ScholarAgent backend...")

    if settings.app_env == "development":
        from backend.db.postgres import init_db
        await init_db()
        logger.info("Database tables initialized.")

    try:
        from backend.db.migrations import run_migrations
        run_migrations(None)
        logger.info("Research feature migrations completed.")
    except Exception as e:
        logger.warning(f"Research feature migrations skipped: {e}")

    init_skills()
    logger.info("ScholarAgent backend is ready.")
    yield

    logger.info("Shutting down ScholarAgent backend...")


app = FastAPI(
    title="ScholarAgent",
    description="科研知识管理与 AI 辅助研究系统",
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

# 注册路由
from backend.api import documents, proxy, admin, notes, research

app.include_router(documents.router, prefix=settings.api_prefix)
app.include_router(proxy.router, prefix=settings.api_prefix)
app.include_router(admin.router, prefix=settings.api_prefix)
app.include_router(notes.router, prefix=settings.api_prefix)
app.include_router(research.router, prefix=settings.api_prefix)


@app.get("/")
async def root():
    return {
        "name": "ScholarAgent",
        "version": "0.5.0",
        "docs": "/docs",
        "description": "科研知识后端，支持深度研究与研究看板",
    }
