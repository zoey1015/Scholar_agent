"""
文本向量化异步任务（完整实现）

Celery 任务：从 DB 读取 chunks → bge-m3 编码 → 写入 Milvus → 更新状态
"""

import logging
import uuid
from datetime import datetime

from sqlalchemy import select, update

from backend.tasks.celery_app import celery_app
from backend.models.document import AsyncTask, Chunk, Document

logger = logging.getLogger(__name__)


def _run_async(coro):
    """在同步的 Celery worker 中运行异步代码"""
    import asyncio
    return asyncio.run(coro)


def _update_task_status(
    task_id: str,
    status: str,
    result_data: dict | None = None,
    error_message: str = "",
):
    """更新任务状态（每次调用独立 session）"""
    from backend.db.postgres import SyncSession

    session = SyncSession()
    try:
        update_data = {
            "status": status,
            "updated_at": datetime.utcnow(),
        }
        if result_data:
            update_data["result_data"] = result_data
        if status == "failed":
            update_data["error_message"] = error_message
        else:
            update_data["error_message"] = None

        session.execute(
            update(AsyncTask)
            .where(AsyncTask.id == uuid.UUID(task_id))
            .values(**update_data)
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _update_document_status(document_id: str, status: str):
    """更新文档状态（每次调用独立 session）"""
    from backend.db.postgres import SyncSession

    session = SyncSession()
    try:
        session.execute(
            update(Document)
            .where(Document.id == uuid.UUID(document_id))
            .values(parse_status=status)
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _get_chunks_by_ids(chunk_ids: list[str]) -> list[Chunk]:
    """按 ID 列表读取 chunks（每次调用独立 session）"""
    from backend.db.postgres import SyncSession

    session = SyncSession()
    try:
        uuids = [uuid.UUID(cid) for cid in chunk_ids]
        stmt = select(Chunk).where(Chunk.id.in_(uuids))
        return list(session.execute(stmt).scalars().all())
    finally:
        session.close()


def _update_chunk_embedding_ids(chunks_for_embedding: list[dict]):
    """回写 chunks.embedding_id（每次调用独立 session）"""
    from backend.db.postgres import SyncSession

    session = SyncSession()
    try:
        for c in chunks_for_embedding:
            session.execute(
                update(Chunk)
                .where(Chunk.id == uuid.UUID(c["chunk_id"]))
                .values(embedding_id=c["chunk_id"])
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _embed_chunks_async(document_id: str, chunk_ids: list[str], user_id: str, task_id: str):
    """同步向量化的核心逻辑"""
    from backend.skills.base import SkillContext
    from backend.skills.registry import skill_registry

    try:
        # 1. 从 DB 读取 chunk 内容
        chunks_db = _get_chunks_by_ids(chunk_ids)
        if not chunks_db:
            logger.warning(f"No chunks found for document {document_id}")
            _update_task_status(
                task_id,
                "success",
                result_data={"document_id": document_id, "embedded": 0},
            )
            return

        # 转换为 Skill 需要的格式
        chunks_for_embedding = [
            {
                "chunk_id": str(c.id),
                "document_id": str(c.document_id),
                "content": c.content,
                "section_title": c.section_title or "",
                "chunk_type": c.chunk_type or "section",
            }
            for c in chunks_db
        ]

        # 2. 调用 EmbeddingSkill
        embedding_skill = skill_registry.get("embedding")
        if embedding_skill is None:
            raise RuntimeError("EmbeddingSkill not registered")

        context = SkillContext(
            user_id=user_id,
            metadata={"chunks": chunks_for_embedding},
        )

        result = _run_async(embedding_skill.execute(context))

        if result.status.value == "failed":
            raise RuntimeError(f"Embedding failed: {result.message}")

        # 3. 更新 chunks 表的 embedding_id
        _update_chunk_embedding_ids(chunks_for_embedding)

        # 4. 更新任务状态为成功
        embedded_count = result.data.get("count", 0)
        _update_task_status(
            task_id,
            "success",
            result_data={
                "document_id": document_id,
                "embedded": embedded_count,
            },
        )

        # 5. 更新文档状态为 ready
        _update_document_status(document_id, "ready")

        try:
            from backend.tasks.upload_hook import on_document_ready
            on_document_ready(document_id, user_id)
        except Exception as hook_error:
            logger.warning(
                f"Document {document_id}: analysis hook failed non-fatally: {hook_error}"
            )

        logger.info(f"Document {document_id}: {embedded_count} chunks embedded successfully")

    except Exception as e:
        logger.error(f"Embedding task failed for document {document_id}: {e}", exc_info=True)
        try:
            _update_task_status(task_id, "failed", error_message=str(e))
        except Exception:
            logger.exception("Failed to update task status after embedding error")
        raise


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=15,
)
def embed_chunks_task(self, document_id: str, chunk_ids: list[str], user_id: str, task_id: str):
    """
    异步向量化文档分块（Celery 入口）

    流程:
    1. 从 chunks 表读取文本内容
    2. 调用 EmbeddingSkill 生成向量并写入 Milvus
    3. 更新 chunks 表的 embedding_id
    4. 更新任务和文档状态
    """
    logger.info(f"[Task] Embedding {len(chunk_ids)} chunks for document {document_id}")

    try:
        _embed_chunks_async(document_id, chunk_ids, user_id, task_id)
    except Exception as e:
        logger.error(f"[Task] Embedding failed for {document_id}: {e}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)
        raise
