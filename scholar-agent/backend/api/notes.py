"""
研究笔记 API

POST /notes/save      保存笔记（调用 ConversationSummarySkill）
GET  /notes           笔记列表
GET  /notes/{id}      笔记详情
POST /notes/search    笔记语义检索
DELETE /notes/{id}    删除笔记
"""

import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from backend.skills.base import SkillContext
from backend.skills.registry import skill_registry
from backend.services.notes_service import get_notes_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notes", tags=["notes"])

MOCK_USER_ID = "00000000-0000-0000-0000-000000000001"


class SaveNoteRequest(BaseModel):
    conversation: str
    title: Optional[str] = None
    source_platform: Optional[str] = ""
    cited_doc_ids: Optional[list[str]] = []


class SearchNotesRequest(BaseModel):
    query: str
    top_k: int = 5


class SaveNoteResponse(BaseModel):
    note_id: str
    title: str
    message: str


@router.post("/save", response_model=SaveNoteResponse)
async def save_note(req: SaveNoteRequest):
    """
    保存研究笔记

    完整流程:
    1. 调用 ConversationSummarySkill 提炼结构化笔记
    2. 存入 PostgreSQL research_notes 表
    3. 向量化标题+摘要+关键词，存入 Milvus notes collection
    """
    skill = skill_registry.get("conversation_summary")
    if not skill:
        raise HTTPException(status_code=500, detail="ConversationSummarySkill not available")

    context = SkillContext(
        user_id=MOCK_USER_ID,
        metadata={
            "conversation": req.conversation,
            "title": req.title or "",
            "source_platform": req.source_platform or "",
            "cited_doc_ids": req.cited_doc_ids or [],
        },
    )

    result = await skill.execute(context)

    if result.status.value == "failed":
        raise HTTPException(status_code=500, detail=result.message)

    # 保存到 DB + Milvus
    notes_service = get_notes_service()
    note_id = notes_service.save_note(
        user_id=MOCK_USER_ID,
        note_data=result.data,
        source_type="api",
    )

    return SaveNoteResponse(
        note_id=note_id,
        title=result.data.get("title", "未命名笔记"),
        message=result.message,
    )


@router.post("/search")
async def search_notes(req: SearchNotesRequest):
    """语义检索研究笔记"""
    notes_service = get_notes_service()
    results = notes_service.search_notes(
        user_id=MOCK_USER_ID,
        query=req.query,
        top_k=req.top_k,
    )
    return {"results": results, "total": len(results)}


@router.get("")
async def list_notes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """笔记列表"""
    notes_service = get_notes_service()
    notes, total = notes_service.list_notes(
        user_id=MOCK_USER_ID,
        page=page,
        page_size=page_size,
    )
    return {"notes": notes, "total": total, "page": page, "page_size": page_size}


@router.get("/{note_id}")
async def get_note(note_id: str):
    """笔记详情"""
    notes_service = get_notes_service()
    note = notes_service.get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    return note


@router.delete("/{note_id}")
async def delete_note(note_id: str):
    """删除笔记"""
    notes_service = get_notes_service()
    deleted = notes_service.delete_note(note_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Note not found")
    return {"note_id": note_id, "deleted": True}
