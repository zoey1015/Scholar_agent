"""
Research API.

SSE deep-research stream, dashboard endpoints, and status updates.
"""

import asyncio
import json
import logging
import re
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/research", tags=["research"])

MOCK_USER_ID = "00000000-0000-0000-0000-000000000001"


class ResearchRequest(BaseModel):
    query: str
    model: str = ""
    session_id: Optional[str] = None


class ResearchBackfillRequest(BaseModel):
    limit: int = 200
    dry_run: bool = True


RESEARCH_SIGNALS = {
    "调研",
    "综述",
    "对比分析",
    "研究现状",
    "研究空白",
    "系统分析",
    "全面分析",
    "深度研究",
    "深入分析",
    "survey",
    "review",
    "compare all",
    "research gap",
    "全面对比",
    "方法论分析",
    "文献调研",
}


def should_use_research(query: str) -> bool:
    q = query.lower()
    return any(keyword in q for keyword in RESEARCH_SIGNALS)


SYNTH_SYSTEM = """你是 ScholarAgent，基于用户的个人知识库回答学术问题。

规则：
- 用 [1][2] 标注引用来源编号
- 如果有论文间的矛盾或互补关系，专门指出
- 如果知识库未检索到相关内容，坦诚说明
- 使用与用户相同的语言
- 结构清晰，内容详实"""


def _build_synth_context(state: dict, evaluation: dict | None = None) -> str:
    parts = []

    results = state.get("search_results", [])
    if results:
        parts.append("=== 检索到的参考资料 ===")
        for idx, result in enumerate(results[:10]):
            score = result.get("score", 0)
            if score < 0.2:
                continue
            rtype = result.get("type", "paper")
            section = result.get("section_title", "") or result.get("title", "")
            content = result.get("content", "")[:500]
            label = f"[{idx + 1}]"
            if section:
                label += f" ({section})"
            if rtype == "note":
                label += " [笔记]"
            parts.append(f"{label}\n{content}")

    relations = state.get("known_relations", [])
    if relations:
        parts.append("=== 已知论文间关系 ===")
        type_labels = {
            "contradiction": "矛盾",
            "complement": "互补",
            "extension": "延伸",
            "overlap": "重叠",
        }
        for relation in relations:
            relation_type = type_labels.get(relation.get("relation_type", ""), relation.get("relation_type", ""))
            parts.append(f"- [{relation_type}] {relation.get('summary', '')}")

    ctx = state.get("user_research_ctx", [])
    if ctx:
        parts.append("=== 用户的研究状态 ===")
        for item in ctx[:5]:
            parts.append(f"- [{item.get('type', '')}] {item.get('content', '')}")

    if evaluation and not evaluation.get("pass", True):
        parts.append("=== 上一版回答的问题 ===")
        for issue in evaluation.get("issues", []):
            parts.append(f"- {issue}")
        suggestion = evaluation.get("suggestion", "")
        if suggestion:
            parts.append(f"改进建议：{suggestion}")

    return "\n\n".join(parts)


async def synthesize_stream(state: dict, evaluation: dict | None = None):
    from backend.llm_adapters.base import resolve_adapter

    context = _build_synth_context(state, evaluation)
    system = SYNTH_SYSTEM + "\n\n" + context
    adapter, model_name = resolve_adapter(state["model"])

    async for token in adapter.chat_stream(
        model=model_name,
        messages=[{"role": "user", "content": state["query"]}],
        system=system,
        temperature=0.5,
        max_tokens=3000,
    ):
        yield token


def evaluate_answer(answer: str, state: dict) -> dict:
    issues = []
    n_results = len(state.get("search_results", []))
    complexity = state.get("complexity", "simple")

    if len(answer) < 80:
        issues.append("回答过于简短")

    if n_results > 0 and not re.search(r"\[\d+\]", answer):
        issues.append("未引用检索到的来源")

    if complexity == "comparison" and len(answer) < 250:
        issues.append("对比分析内容不够充分")

    relations = state.get("known_relations", [])
    if relations and not any(keyword in answer for keyword in ["矛盾", "互补", "延伸", "重叠", "contradict", "complement"]):
        issues.append("未涉及已知的论文间关系")

    if issues:
        needs_data = any(keyword in " ".join(issues) for keyword in ["检索", "来源", "不足"])
        return {"pass": False, "issues": issues, "suggestion": "请补充上述不足", "needs_data": needs_data}

    return {"pass": True, "issues": [], "suggestion": "", "needs_data": False}


def _build_sources_payload(results: list[dict], limit: int = 12) -> list[dict]:
    """Normalize retrieval results for frontend reference rendering."""
    sources = []
    for result in results:
        score = result.get("score", 0)
        if score < 0.2:
            continue
        sources.append(
            {
                "type": result.get("type", "paper"),
                "chunk_id": result.get("chunk_id"),
                "note_id": result.get("note_id"),
                "document_id": result.get("document_id"),
                "section_title": result.get("section_title", "") or result.get("title", ""),
                "content": result.get("content", "")[:300],
                "score": score,
            }
        )
    return sources[:limit]


RESEARCH_STATE_PROMPT = """从以下研究问答中提取用户的研究状态。

提取以下类型：
- question: 用户关注的研究问题
- hypothesis: 待验证的假设
- conclusion: 已确认的结论
- direction: 后续研究方向

只输出 JSON（不要任何别的文字）：
{"items": [{"type": "question", "content": "..."}]}

如果信息不足以提取，输出 {"items": []}"""


async def extract_and_save_research_state(user_id: str, query: str, answer: str, session_id: str = ""):
    from backend.config import get_settings
    from backend.llm_adapters.base import resolve_adapter
    from backend.services.research_state_service import save_research_items

    settings = get_settings()
    try:
        try:
            adapter, model_name = resolve_adapter(settings.light_llm_model)
        except ValueError:
            adapter, model_name = resolve_adapter(settings.default_llm_model)

        content = f"用户问题：{query}\n\n回答：{answer[:1500]}"
        response = await adapter.chat(
            model=model_name,
            messages=[{"role": "user", "content": content}],
            system=RESEARCH_STATE_PROMPT,
            temperature=0.2,
            max_tokens=500,
        )

        match = re.search(r"\{.*\}", response, re.DOTALL)
        if match:
            data = json.loads(match.group())
            items = data.get("items", [])
            if items:
                save_research_items(user_id, items, session_id)
                logger.info(f"Saved {len(items)} research state items")
    except Exception as exc:
        logger.warning(f"Research state extraction failed: {exc}")


@router.post("/stream")
async def research_stream(req: ResearchRequest):
    from backend.config import get_settings
    from backend.graph.graph import build_initial_state, get_research_graph
    from backend.graph.nodes import planner_replan_node
    from backend.llm_adapters.base import resolve_adapter

    settings = get_settings()
    model = req.model or settings.default_llm_model
    session_id = req.session_id or str(uuid.uuid4())

    try:
        resolve_adapter(model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    async def event_generator():
        graph = get_research_graph()
        state = build_initial_state(req.query, MOCK_USER_ID, model)
        replan_count = 0
        evaluation = None

        try:
            while replan_count <= 2:
                need_graph = replan_count == 0 or (evaluation and evaluation.get("needs_data", False))

                if need_graph:
                    if replan_count > 0 and evaluation:
                        state["_replan_issues"] = evaluation.get("issues", [])
                        replan_result = await planner_replan_node(state)
                        replan_logs = replan_result.pop("step_logs", [])
                        if replan_logs:
                            state.setdefault("step_logs", []).extend(replan_logs)
                        state.update(replan_result)

                        yield _sse({"type": "replan", "reason": "; ".join(evaluation.get("issues", [])), "count": replan_count})

                    try:
                        async for event in graph.astream(state, stream_mode="updates"):
                            for node_name, updates in event.items():
                                for log in updates.get("step_logs", []):
                                    yield _sse({"type": "log", "message": log})

                                if node_name == "classify_and_plan" and "plan" in updates:
                                    yield _sse({"type": "plan", "steps": updates["plan"], "complexity": updates.get("complexity", "")})

                                if node_name == "executor" and "plan" in updates:
                                    for step in updates["plan"]:
                                        if step.get("status") in ("done", "failed"):
                                            yield _sse({
                                                "type": "progress",
                                                "step_id": step.get("id"),
                                                "action": step.get("action"),
                                                "status": step.get("status"),
                                                "summary": step.get("result_summary", ""),
                                            })

                                if "known_relations" in updates and updates["known_relations"]:
                                    yield _sse({"type": "relation", "data": updates["known_relations"]})

                                if "user_research_ctx" in updates and updates["user_research_ctx"]:
                                    yield _sse({"type": "research_ctx", "data": updates["user_research_ctx"]})

                                if "search_results" in updates and updates["search_results"]:
                                    yield _sse({"type": "sources", "data": _build_sources_payload(updates["search_results"])})

                                new_logs = updates.pop("step_logs", [])
                                if new_logs:
                                    state.setdefault("step_logs", []).extend(new_logs)
                                state.update(updates)
                    except Exception as exc:
                        logger.error(f"Graph execution failed: {exc}", exc_info=True)
                        yield _sse({"type": "error", "message": f"数据收集失败：{str(exc)[:200]}"})
                        yield _sse({"type": "done"})
                        return

                yield _sse({"type": "log", "message": "✍️ Synthesizer: 开始生成回答..."})

                if state.get("search_results"):
                    yield _sse({"type": "sources", "data": _build_sources_payload(state["search_results"])})

                answer_chunks = []
                try:
                    async for token in synthesize_stream(state, evaluation):
                        answer_chunks.append(token)
                        yield _sse({"type": "token", "content": token})
                except Exception as exc:
                    error_msg = f"生成失败：{str(exc)[:200]}"
                    yield _sse({"type": "token", "content": error_msg})
                    answer_chunks.append(error_msg)

                full_answer = "".join(answer_chunks)
                evaluation = evaluate_answer(full_answer, state)

                if evaluation["pass"]:
                    yield _sse({"type": "log", "message": "✅ Evaluator: 质量检查通过"})
                    asyncio.create_task(extract_and_save_research_state(MOCK_USER_ID, req.query, full_answer, session_id))
                    yield _sse({"type": "done"})
                    return

                replan_count += 1
                if replan_count > 2:
                    yield _sse({"type": "log", "message": "⚠️ Evaluator: 重规划次数耗尽，使用当前结果"})
                    yield _sse({"type": "done"})
                    return

                issues_str = "; ".join(evaluation.get("issues", []))
                yield _sse({"type": "log", "message": f"⚠️ Evaluator: {issues_str} → 触发重规划"})

                if not evaluation.get("needs_data", False):
                    yield _sse({"type": "replan", "reason": issues_str, "count": replan_count})
                    continue

            yield _sse({"type": "done"})
        except Exception as exc:
            logger.error(f"Research stream error: {exc}", exc_info=True)
            yield _sse({"type": "error", "message": str(exc)[:300]})
            yield _sse({"type": "done"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/run")
async def research_run(req: ResearchRequest):
    from backend.config import get_settings
    from backend.graph.graph import build_initial_state, get_research_graph
    from backend.llm_adapters.base import resolve_adapter

    settings = get_settings()
    model = req.model or settings.default_llm_model
    session_id = req.session_id or str(uuid.uuid4())

    graph = get_research_graph()
    state = build_initial_state(req.query, MOCK_USER_ID, model)

    try:
        final_state = await graph.ainvoke(state)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Graph failed: {exc}")

    adapter, model_name = resolve_adapter(model)
    system = SYNTH_SYSTEM + "\n\n" + _build_synth_context(final_state)

    try:
        answer = await adapter.chat(
            model=model_name,
            messages=[{"role": "user", "content": req.query}],
            system=system,
            temperature=0.5,
            max_tokens=3000,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM failed: {exc}")

    evaluation = evaluate_answer(answer, final_state)

    return {
        "research_id": session_id,
        "answer": answer,
        "plan": final_state.get("plan", []),
        "step_logs": final_state.get("step_logs", []),
        "known_relations": final_state.get("known_relations", []),
        "user_research_ctx": final_state.get("user_research_ctx", []),
        "complexity": final_state.get("complexity", ""),
        "evaluation": evaluation,
        "sources": _build_sources_payload(final_state.get("search_results", []), limit=50),
    }


@router.get("/dashboard")
async def research_dashboard():
    from backend.services.relations_service import get_all_relations
    from backend.services.research_state_service import get_all_items, get_unread_notifications

    return {
        "research_items": get_all_items(MOCK_USER_ID),
        "relations": get_all_relations(limit=30),
        "notifications": get_unread_notifications(MOCK_USER_ID),
    }


@router.post("/notifications/{nid}/read")
async def mark_notification_read(nid: str):
    from backend.services.research_state_service import mark_read

    return {"success": mark_read(nid)}


@router.post("/state/{item_id}/status")
async def update_research_state(item_id: str, status: str = "archived"):
    from backend.services.research_state_service import update_item_status

    return {"success": update_item_status(item_id, status)}


@router.post("/backfill")
async def backfill_research_analysis(req: ResearchBackfillRequest):
    """Backfill analysis pipeline for historical documents that missed post-upload hooks."""
    from backend.tasks.analysis_tasks import backfill_existing_documents

    limit = max(1, min(int(req.limit), 1000))
    result = backfill_existing_documents(limit=limit, dry_run=req.dry_run)
    result["limit"] = limit
    return result


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
