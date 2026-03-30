"""
文档管理 API（上传、检索、详情、删除）
"""

import uuid
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from backend.skills.base import SkillContext
from backend.skills.registry import skill_registry

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
async def upload_document(file: UploadFile = File(...)):
    """
    上传论文/专利 PDF

    流程:
    1. 保存文件到 MinIO
    2. 创建 document 记录（parse_status=pending）
    3. 触发异步任务：解析 → 向量化
    4. 返回 document_id 和 task_id
    """
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    # TODO Phase 1: 实现文件存储和异步任务触发
    doc_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    return {
        "document_id": doc_id,
        "task_id": task_id,
        "filename": file.filename,
        "status": "pending",
        "message": "File uploaded. Parsing will start shortly.",
    }


@router.post("/search", response_model=SearchResponse)
async def search_documents(req: SearchRequest):
    """
    混合检索知识库

    调用 RetrievalSkill 执行 BM25 + 向量 + Rerank
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
    return SearchResponse(
        results=result.data.get("results", []),
        total=result.data.get("total", 0),
    )


@router.get("")
async def list_documents(
    doc_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    """文档列表（支持筛选、分页）"""
    # TODO Phase 1: 查询 PostgreSQL
    return {"documents": [], "total": 0, "page": page, "page_size": page_size}


@router.get("/{doc_id}")
async def get_document(doc_id: str):
    """文档详情"""
    # TODO Phase 1: 查询 PostgreSQL
    return {"document_id": doc_id, "detail": "not implemented"}


@router.delete("/{doc_id}")
async def delete_document(doc_id: str):
    """删除文档（含向量索引清理）"""
    # TODO Phase 1: 删除 PostgreSQL 记录 + Milvus 向量 + MinIO 文件
    return {"document_id": doc_id, "deleted": True}
