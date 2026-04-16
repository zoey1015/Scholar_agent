"""
文档管理 API（完整实现）

上传 PDF → MinIO 存储 → DB 记录 → 触发异步解析
检索 → RetrievalSkill 向量检索
列表/详情/删除 → PostgreSQL CRUD
"""

import uuid
import logging
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.postgres import get_db
from backend.db import crud
from backend.skills.base import SkillContext
from backend.skills.registry import skill_registry
from backend.services.minio_service import get_minio_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    doc_type: str = "all"       # "all" / "paper" / "patent"
    language: str = "all"       # "all" / "en" / "zh"


class SearchResponse(BaseModel):
    results: list[dict]
    total: int


# TODO: 替换为真实的用户认证
MOCK_USER_ID = "00000000-0000-0000-0000-000000000001"


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    doc_type: str = Query("paper", description="文档类型: paper / patent"),
    language: str = Query("en", description="语言: en / zh / mixed"),
    db: AsyncSession = Depends(get_db),
):
    """
    上传论文/专利 PDF

    完整流程:
    1. 读取文件内容
    2. 上传到 MinIO 对象存储
    3. 创建 document 记录（状态: pending）
    4. 创建 async_task 记录
    5. 触发 Celery 异步解析任务
    6. 返回 document_id 和 task_id
    """
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    # 1. 读取文件内容
    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(file_bytes) > 100 * 1024 * 1024:  # 100MB 限制
        raise HTTPException(status_code=400, detail="File too large (max 100MB).")

    # 2. 上传到 MinIO
    doc_id = str(uuid.uuid4())
    object_name = f"papers/{doc_id}.pdf"

    try:
        minio = get_minio_service()
        minio.upload_file(file_bytes, object_name)
    except Exception as e:
        logger.error(f"MinIO upload failed: {e}")
        raise HTTPException(status_code=500, detail="File storage failed.")

    # 3. 创建 document 记录
    doc = await crud.create_document(
        db=db,
        user_id=MOCK_USER_ID,
        title=file.filename.replace(".pdf", ""),    # 临时标题，解析后更新
        doc_type=doc_type,
        language=language,
        source="upload",
        file_path=object_name,
    )

    # 4. 创建异步任务记录
    task = await crud.create_task(
        db=db,
        user_id=MOCK_USER_ID,
        task_type="parse_document",
        input_data={
            "document_id": str(doc.id),
            "file_path": object_name,
            "filename": file.filename,
        },
    )
    await db.commit()

    # 5. 触发 Celery 异步解析任务
    from backend.tasks.parse_tasks import parse_document_task
    parse_document_task.delay(
        document_id=str(doc.id),
        file_path=object_name,
        user_id=MOCK_USER_ID,
        task_id=str(task.id),
    )

    logger.info(f"Upload complete: doc={doc.id}, task={task.id}, file={file.filename}")

    return {
        "document_id": str(doc.id),
        "task_id": str(task.id),
        "filename": file.filename,
        "status": "pending",
        "message": "File uploaded. Parsing will start shortly.",
    }


@router.post("/search", response_model=SearchResponse)
async def search_documents(req: SearchRequest):
    """
    检索知识库

    调用 RetrievalSkill 执行向量语义检索
    """
    retrieval_skill = skill_registry.get("retrieval")
    if not retrieval_skill:
        raise HTTPException(status_code=500, detail="RetrievalSkill not available.")

    context = SkillContext(
        user_id=MOCK_USER_ID,
        query=req.query,
        metadata={
            "top_k": req.top_k,
            "doc_type": req.doc_type,
            "language": req.language,
        },
    )

    result = await retrieval_skill.execute(context)

    if result.data is None:
        return SearchResponse(results=[], total=0)

    return SearchResponse(
        results=result.data.get("results", []),
        total=result.data.get("total", 0),
    )


@router.get("")
async def list_documents(
    doc_type: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """文档列表（支持筛选、分页）"""
    docs, total = await crud.list_documents(
        db=db,
        user_id=MOCK_USER_ID,
        doc_type=doc_type,
        page=page,
        page_size=page_size,
    )

    return {
        "documents": [
            {
                "id": str(d.id),
                "title": d.title,
                "doc_type": d.doc_type,
                "language": d.language,
                "authors": d.authors,
                "year": d.year,
                "tags": d.tags,
                "parse_status": d.parse_status,
                "quality_score": d.quality_score,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in docs
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{doc_id}")
async def get_document(doc_id: str, db: AsyncSession = Depends(get_db)):
    """文档详情（含 chunks 信息）"""
    doc = await crud.get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    # 获取 chunks
    chunks = await crud.get_chunks_by_document(db, doc_id)

    return {
        "document": {
            "id": str(doc.id),
            "title": doc.title,
            "doc_type": doc.doc_type,
            "language": doc.language,
            "authors": doc.authors,
            "abstract": doc.abstract,
            "year": doc.year,
            "tags": doc.tags,
            "source": doc.source,
            "file_path": doc.file_path,
            "parse_status": doc.parse_status,
            "quality_score": doc.quality_score,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
        },
        "chunks": [
            {
                "id": str(c.id),
                "chunk_index": c.chunk_index,
                "section_title": c.section_title,
                "chunk_type": c.chunk_type,
                "content": c.content[:200] + "..." if len(c.content) > 200 else c.content,
                "token_count": c.token_count,
                "has_embedding": c.embedding_id is not None,
            }
            for c in chunks
        ],
        "chunk_count": len(chunks),
    }


@router.delete("/{doc_id}")
async def delete_document(doc_id: str, db: AsyncSession = Depends(get_db)):
    """
    删除文档

    1. 删除 Milvus 中的向量
    2. 删除 MinIO 中的文件
    3. 删除 PostgreSQL 中的记录（chunks 通过 CASCADE 自动删除）
    """
    try:
        uuid.UUID(doc_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid document id format. UUID required.")

    doc = await crud.get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    # 1. 删除 Milvus 向量
    try:
        from backend.services.milvus_service import get_milvus_service
        milvus = get_milvus_service()
        milvus.delete_by_document(doc_id)
    except Exception as e:
        logger.warning(f"Failed to delete Milvus vectors for {doc_id}: {e}")

    # 2. 删除 MinIO 文件
    if doc.file_path:
        try:
            minio = get_minio_service()
            minio.delete_file(doc.file_path)
        except Exception as e:
            logger.warning(f"Failed to delete MinIO file for {doc_id}: {e}")

    # 3. 删除数据库记录
    deleted = await crud.delete_document(db, doc_id)

    return {"document_id": doc_id, "deleted": deleted}


@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str, db: AsyncSession = Depends(get_db)):
    """查询异步任务状态"""
    task = await crud.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    return {
        "task_id": str(task.id),
        "task_type": task.task_type,
        "status": task.status,
        "result_data": task.result_data,
        "error_message": task.error_message,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }
