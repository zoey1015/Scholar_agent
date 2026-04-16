"""
系统管理 API：健康检查、Skill 列表、执行链路查询
"""

from fastapi import APIRouter
from backend.skills.registry import skill_registry

router = APIRouter(tags=["admin"])


@router.get("/health")
async def health_check():
    return {"status": "ok", "skills_loaded": skill_registry.count}


@router.get("/skills")
async def list_skills():
    """列出所有已注册的 Skill"""
    return {"skills": skill_registry.list_skills()}


@router.get("/traces/{session_id}")
async def get_traces(session_id: str):
    """查看 Agent 执行链路"""
    # TODO Phase 3: 查询 agent_traces 表
    return {"session_id": session_id, "traces": []}


@router.get("/quality/dashboard")
async def quality_dashboard():
    """入库质量指标仪表盘"""
    # TODO Phase 3: 聚合查询 documents 表的 quality_score
    return {
        "total_documents": 0,
        "parse_success_rate": 0.0,
        "avg_field_completeness": 0.0,
        "needs_review_count": 0,
    }
