"""
代理编排 API

对不支持 MCP 的模型，提供完整的 RAG Pipeline:
检索知识库 → 拼接上下文 → 调用目标模型 → 返回回答

这是覆盖所有非 Claude 模型的关键通道。
"""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from backend.skills.base import SkillContext
from backend.skills.registry import skill_registry
from backend.llm_adapters.base import resolve_adapter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/proxy", tags=["proxy"])

MOCK_USER_ID = "00000000-0000-0000-0000-000000000001"


class ProxyChatRequest(BaseModel):
    query: str
    model: str = "claude-sonnet-4-20250514"
    auto_retrieve: bool = True
    auto_save_note: bool = False
    retrieve_options: Optional[dict] = None     # {"top_k": 5, "doc_type": "all"}
    system_prompt: str = ""


class ProxyChatResponse(BaseModel):
    answer: str
    model_used: str
    sources: list[dict] = []        # 引用的知识库来源
    note_id: Optional[str] = None   # 若 auto_save_note=True，返回笔记 ID


@router.post("/chat", response_model=ProxyChatResponse)
async def proxy_chat(req: ProxyChatRequest):
    """
    代理编排对话

    完整流程 (LangGraph 状态机):
    1. 接收用户 query
    2. RetrievalSkill: 混合检索知识库
    3. 上下文拼接: 将检索结果注入 system prompt
    4. LLM 调用: 通过 LLM 适配层调用目标模型
    5. (可选) ConversationSummarySkill: 自动总结并存入知识库
    6. 返回回答 + 引用来源
    """

    sources = []

    # Step 1: 检索知识库上下文
    if req.auto_retrieve:
        retrieval_skill = skill_registry.get("retrieval")
        if retrieval_skill:
            opts = req.retrieve_options or {}
            context = SkillContext(
                user_id=MOCK_USER_ID,
                query=req.query,
                metadata={
                    "top_k": opts.get("top_k", 5),
                    "doc_type": opts.get("doc_type", "all"),
                },
            )
            result = await retrieval_skill.execute(context)
            sources = result.data.get("results", []) if result.data else []

    # Step 2: 拼接上下文到 system prompt
    system = req.system_prompt or "你是一个专业的科研助手。请基于提供的参考资料回答问题。如果参考资料中没有相关信息，请如实说明。"
    if sources:
        context_text = "\n\n".join(
            f"[来源 {i+1}] {s.get('section_title', '')}\n{s.get('content', '')}"
            for i, s in enumerate(sources)
        )
        system += f"\n\n以下是从知识库中检索到的参考资料：\n{context_text}"

    # Step 3: 调用目标模型
    try:
        adapter, model_name = resolve_adapter(req.model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    messages = [{"role": "user", "content": req.query}]

    try:
        answer = await adapter.chat(
            model=model_name,
            messages=messages,
            system=system,
        )
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        raise HTTPException(status_code=502, detail=f"Model call failed: {str(e)}")

    # Step 4: (可选) 自动保存笔记
    note_id = None
    if req.auto_save_note:
        # TODO Phase 2: 调用 ConversationSummarySkill
        pass

    return ProxyChatResponse(
        answer=answer,
        model_used=req.model,
        sources=sources,
        note_id=note_id,
    )
