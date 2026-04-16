"""
数据库 CRUD 操作

所有数据库读写都通过这个模块，不在 API 层直接写 SQL。
"""

import uuid
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.document import Document, Chunk, ResearchNote, AsyncTask

logger = logging.getLogger(__name__)


# ========================
# Document CRUD
# ========================

async def create_document(
    db: AsyncSession,
    user_id: str,
    title: str,
    doc_type: str = "paper",
    language: str = "en",
    source: str = "upload",
    file_path: str = "",
    external_id: str = "",
) -> Document:
    """创建文档记录"""
    doc = Document(
        id=uuid.uuid4(),
        user_id=uuid.UUID(user_id),
        title=title,
        doc_type=doc_type,
        language=language,
        source=source,
        file_path=file_path,
        external_id=external_id,
        parse_status="pending",
    )
    db.add(doc)
    await db.flush()
    logger.info(f"Created document: {doc.id} - {title}")
    return doc


async def update_document_parsed(
    db: AsyncSession,
    document_id: str,
    parsed_data: dict,
    title: str = "",
    authors: list = None,
    abstract: str = "",
    year: int = None,
    keywords: list = None,
    quality_score: dict = None,
) -> None:
    """更新文档的解析结果"""
    update_data = {
        "parsed_data": parsed_data,
        "parse_status": "success",
    }
    if title:
        update_data["title"] = title
    if authors is not None:
        update_data["authors"] = authors
    if abstract:
        update_data["abstract"] = abstract
    if year:
        update_data["year"] = year
    if keywords:
        update_data["tags"] = keywords
    if quality_score:
        update_data["quality_score"] = quality_score

    stmt = (
        update(Document)
        .where(Document.id == uuid.UUID(document_id))
        .values(**update_data)
    )
    await db.execute(stmt)
    await db.commit()
    logger.info(f"Updated document parsed data: {document_id}")


async def update_document_status(
    db: AsyncSession,
    document_id: str,
    status: str,
    error_message: str = "",
) -> None:
    """更新文档解析状态"""
    update_data = {"parse_status": status}
    stmt = (
        update(Document)
        .where(Document.id == uuid.UUID(document_id))
        .values(**update_data)
    )
    await db.execute(stmt)
    await db.commit()


async def get_document(db: AsyncSession, document_id: str) -> Optional[Document]:
    """获取单个文档"""
    stmt = select(Document).where(Document.id == uuid.UUID(document_id))
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_documents(
    db: AsyncSession,
    user_id: str,
    doc_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Document], int]:
    """分页查询文档列表"""
    stmt = select(Document).where(Document.user_id == uuid.UUID(user_id))

    if doc_type and doc_type != "all":
        stmt = stmt.where(Document.doc_type == doc_type)

    # 总数
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # 分页
    stmt = stmt.order_by(Document.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    docs = result.scalars().all()

    return list(docs), total


async def delete_document(db: AsyncSession, document_id: str) -> bool:
    """删除文档（chunks 通过 CASCADE 自动删除）"""
    stmt = delete(Document).where(Document.id == uuid.UUID(document_id))
    result = await db.execute(stmt)
    await db.commit()
    return result.rowcount > 0


# ========================
# Chunk CRUD
# ========================

async def create_chunks_batch(db: AsyncSession, chunks: list[dict]) -> list[str]:
    """批量创建 chunk 记录"""
    chunk_ids = []
    for c in chunks:
        chunk = Chunk(
            id=uuid.UUID(c["chunk_id"]),
            document_id=uuid.UUID(c["document_id"]),
            chunk_index=c["chunk_index"],
            content=c["content"],
            section_title=c.get("section_title", ""),
            chunk_type=c.get("chunk_type", "section"),
            token_count=c.get("token_count", 0),
        )
        db.add(chunk)
        chunk_ids.append(c["chunk_id"])

    await db.flush()
    logger.info(f"Created {len(chunk_ids)} chunks for document {chunks[0]['document_id']}")
    return chunk_ids


async def update_chunk_embedding_id(
    db: AsyncSession,
    chunk_id: str,
    embedding_id: str,
) -> None:
    """更新 chunk 的 embedding_id（向量化后回写）"""
    stmt = (
        update(Chunk)
        .where(Chunk.id == uuid.UUID(chunk_id))
        .values(embedding_id=embedding_id)
    )
    await db.execute(stmt)


async def get_chunks_by_document(
    db: AsyncSession,
    document_id: str,
) -> list[Chunk]:
    """获取文档的所有 chunks"""
    stmt = (
        select(Chunk)
        .where(Chunk.document_id == uuid.UUID(document_id))
        .order_by(Chunk.chunk_index)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_chunks_by_ids(
    db: AsyncSession,
    chunk_ids: list[str],
) -> list[Chunk]:
    """按 ID 列表获取 chunks（检索结果回查用）"""
    uuids = [uuid.UUID(cid) for cid in chunk_ids]
    stmt = select(Chunk).where(Chunk.id.in_(uuids))
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ========================
# AsyncTask CRUD
# ========================

async def create_task(
    db: AsyncSession,
    user_id: str,
    task_type: str,
    input_data: dict = None,
) -> AsyncTask:
    """创建异步任务记录"""
    task = AsyncTask(
        id=uuid.uuid4(),
        user_id=uuid.UUID(user_id),
        task_type=task_type,
        status="pending",
        input_data=input_data or {},
    )
    db.add(task)
    await db.flush()
    return task


async def update_task_status(
    db: AsyncSession,
    task_id: str,
    status: str,
    result_data: dict = None,
    error_message: str = "",
) -> None:
    """更新任务状态"""
    update_data = {"status": status, "updated_at": datetime.utcnow()}
    if result_data:
        update_data["result_data"] = result_data
    if status == "failed":
        update_data["error_message"] = error_message
    else:
        update_data["error_message"] = None

    stmt = (
        update(AsyncTask)
        .where(AsyncTask.id == uuid.UUID(task_id))
        .values(**update_data)
    )
    await db.execute(stmt)
    await db.commit()


async def get_task(db: AsyncSession, task_id: str) -> Optional[AsyncTask]:
    """获取任务状态"""
    stmt = select(AsyncTask).where(AsyncTask.id == uuid.UUID(task_id))
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
