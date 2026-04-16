"""
论文/专利解析异步任务（完整实现）

Celery 任务：从 MinIO 下载 PDF → GROBID 解析 → 写入 DB → 触发向量化
"""

import logging
import uuid
from datetime import datetime

from sqlalchemy import update

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


def _update_document_parsed(
    document_id: str,
    parsed_data: dict,
    title: str,
    authors: list | None,
    abstract: str,
    year: int | None,
    keywords: list | None,
    quality_score: dict | None,
):
    """更新文档解析结果（每次调用独立 session）"""
    from backend.db.postgres import SyncSession

    session = SyncSession()
    try:
        update_data = {
            "parsed_data": parsed_data,
            "parse_status": "success",
            "title": title or "Untitled",
            "authors": authors or [],
            "abstract": abstract or "",
            "tags": keywords or [],
        }
        if year:
            update_data["year"] = year
        if quality_score:
            update_data["quality_score"] = quality_score

        session.execute(
            update(Document)
            .where(Document.id == uuid.UUID(document_id))
            .values(**update_data)
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _create_chunks_batch(chunks: list[dict]):
    """写入 chunks（每次调用独立 session）"""
    from backend.db.postgres import SyncSession

    session = SyncSession()
    try:
        for c in chunks:
            session.add(
                Chunk(
                    id=uuid.UUID(c["chunk_id"]),
                    document_id=uuid.UUID(c["document_id"]),
                    chunk_index=c["chunk_index"],
                    content=c["content"],
                    section_title=c.get("section_title", ""),
                    chunk_type=c.get("chunk_type", "section"),
                    token_count=c.get("token_count", 0),
                )
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _parse_document_async(document_id: str, file_path: str, user_id: str, task_id: str):
    """同步解析文档的核心逻辑"""
    from backend.skills.base import SkillContext
    from backend.skills.registry import skill_registry

    try:
        # 1. 更新任务状态为 processing
        _update_task_status(task_id, "processing")
        _update_document_status(document_id, "processing")

        # 2. 调用 PaperParserSkill 解析
        parser_skill = skill_registry.get("paper_parser")
        if parser_skill is None:
            raise RuntimeError("PaperParserSkill not registered")

        context = SkillContext(
            user_id=user_id,
            metadata={
                "file_path": file_path,
                "document_id": document_id,
            },
        )

        result = _run_async(parser_skill.execute(context))

        if result.status.value == "failed":
            raise RuntimeError(f"Parse failed: {result.message}")

        parsed_data = result.data["parsed_data"]
        chunks = result.data["chunks"]
        quality_score = result.data["quality_score"]

        # 3. 更新 documents 表
        year = None
        if parsed_data.get("references"):
            years = [int(r["year"]) for r in parsed_data["references"] if r.get("year", "").isdigit()]
            if years:
                year = max(years)

        _update_document_parsed(
            document_id=document_id,
            parsed_data=parsed_data,
            title=parsed_data.get("title", "") or "Untitled",
            authors=parsed_data.get("authors", []),
            abstract=parsed_data.get("abstract", ""),
            year=year,
            keywords=parsed_data.get("keywords", []),
            quality_score=quality_score,
        )

        # 4. 写入 chunks 表
        if chunks:
            _create_chunks_batch(chunks)

            # 5. 触发向量化任务
            from backend.tasks.embedding_tasks import embed_chunks_task
            chunk_ids = [c["chunk_id"] for c in chunks]
            embed_chunks_task.delay(document_id, chunk_ids, user_id, task_id)

            logger.info(f"Document {document_id}: parsed, {len(chunks)} chunks created, embedding triggered")
        else:
            _update_task_status(
                task_id,
                "success",
                result_data={"document_id": document_id, "chunks": 0},
            )
            logger.info(f"Document {document_id}: parsed but no chunks generated")

    except Exception as e:
        logger.error(f"Document {document_id} parse task failed: {e}", exc_info=True)
        try:
            _update_task_status(task_id, "failed", error_message=str(e))
            _update_document_status(document_id, "failed")
        except Exception:
            logger.exception("Failed to update task/document status after parse error")
        raise


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def parse_document_task(self, document_id: str, file_path: str, user_id: str, task_id: str):
    """
    异步解析文档（Celery 入口）

    流程:
    1. 更新状态为 processing
    2. 调用 PaperParserSkill（GROBID 解析 + 分块）
    3. 结果写入 documents + chunks 表
    4. 触发 embed_chunks_task 做向量化
    """
    logger.info(f"[Task] Parsing document {document_id}: {file_path}")

    try:
        _parse_document_async(document_id, file_path, user_id, task_id)
    except Exception as e:
        logger.error(f"[Task] Document {document_id} failed: {e}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)
        raise
