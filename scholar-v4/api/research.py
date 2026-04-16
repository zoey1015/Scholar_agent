"""
深度研究 API - SSE 流式

完整流程：
  while replan_count <= 2:
      Phase 1: 运行 LangGraph 图 → 收集数据（SSE 推送进度）
      Phase 2: 流式 LLM 生成回答（SSE 推送 token）
      Phase 3: 质量检查（Evaluator）
      Phase 4: 通过 → 写入 research_state → done
               不通过且需要补数据 → 注入新步骤 → 回到 Phase 1
               不通过但数据够了 → 回到 Phase 2

SSE 事件类型：
  plan:      {"type":"plan", "steps":[...], "complexity":"..."}
  progress:  {"type":"progress", "step_id":1, "status":"done", "summary":"..."}
  log:       {"type":"log", "message":"..."}
  relation:  {"type":"relation", "data":[...]}
  token:     {"type":"token", "content":"一段文字"}
  replan:    {"type":"replan", "reason":"...", "count":1}
  done:      {"type":"done"}
  error:     {"type":"error", "message":"..."}
"""

import json
import logging
import re
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/research", tags=["research"])

MOCK_USER_ID = "00000000-0000-0000-0000-000000000001"


class ResearchRequest(BaseModel):
    query: str
    model: str = ""
    session_id: Optional[str] = None


# ================================================================
# 判断是否走深度研究路径
# ================================================================

RESEARCH_SIGNALS = {
    "调研", "综述", "对比分析", "研究现状", "研究空白",
    "系统分析", "全面分析", "深度研究", "深入分析",
    "survey", "review", "compare all", "research gap",
    "全面对比", "方法论分析", "文献调研",
}


def should_use_research(query: str) -> bool:
    """判断是否走深度研究路径（供 proxy 路由用）"""
    q = query.lower()
    return any(kw in q for kw in RESEARCH_SIGNALS)


# ================================================================
# Synthesizer（图外，流式 LLM）
# ================================================================

SYNTH_SYSTEM = """你是 ScholarAgent，基于用户的个人知识库回答学术问题。

规则：
- 用 [1][2] 标注引用来源编号
- 如果有论文间的矛盾或互补关系，专门指出
- 如果知识库未检索到相关内容，坦诚说明
- 使用与用户相同的语言
- 结构清晰，内容详实"""


def _build_synth_context(state: dict, evaluation: dict = None) -> str:
    """为 Synthesizer 组装 context"""
    parts = []

    # 检索结果
    results = state.get("search_results", [])
    if results:
        parts.append("=== 检索到的参考资料 ===")
        for i, r in enumerate(results[:10]):
            score = r.get("score", 0)
            if score < 0.2:
                continue
            rtype = r.get("type", "paper")
            section = r.get("section_title", "") or r.get("title", "")
            content = r.get("content", "")[:500]
            label = f"[{i+1}]"
            if section:
                label += f" ({section})"
            if rtype == "note":
                label += " [笔记]"
            parts.append(f"{label}\n{content}")

    # 预计算的论文关系
    relations = state.get("known_relations", [])
    if relations:
        parts.append("=== 已知论文间关系 ===")
        type_labels = {
            "contradiction": "矛盾",
            "complement": "互补",
            "extension": "延伸",
            "overlap": "重叠",
        }
        for r in relations:
            rtype = type_labels.get(r.get("relation_type", ""), r.get("relation_type", ""))
            parts.append(f"- [{rtype}] {r.get('summary', '')}")

    # 用户研究状态
    ctx = state.get("user_research_ctx", [])
    if ctx:
        parts.append("=== 用户的研究状态 ===")
        for item in ctx[:5]:
            parts.append(f"- [{item.get('type', '')}] {item.get('content', '')}")

    # 如果有评估反馈（Replan 场景）
    if evaluation and not evaluation.get("pass", True):
        parts.append("=== 上一版回答的问题 ===")
        for issue in evaluation.get("issues", []):
            parts.append(f"- {issue}")
        suggestion = evaluation.get("suggestion", "")
        if suggestion:
            parts.append(f"改进建议：{suggestion}")

    return "\n\n".join(parts)


async def synthesize_stream(state: dict, evaluation: dict = None):
    """
    Synthesizer: 流式生成回答

    这是整个研究流程中唯一的 LLM 生成调用。
    yield 每个 token。
    """
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


# ================================================================
# Evaluator（图外，规则 + 可选 LLM）
# ================================================================

def evaluate_answer(answer: str, state: dict) -> dict:
    """
    质量评估

    先跑规则检查（0ms），能拦住大部分问题。
    只有 comparison 类问题才考虑 LLM 评估（暂不实现，留接口）。

    返回: {"pass": bool, "issues": [...], "suggestion": "...", "needs_data": bool}
    """
    issues = []
    n_results = len(state.get("search_results", []))
    complexity = state.get("complexity", "simple")

    # 检查 1: 回答太短
    if len(answer) < 80:
        issues.append("回答过于简短")

    # 检查 2: 有检索结果但未引用
    if n_results > 0 and not re.search(r'\[\d+\]', answer):
        issues.append("未引用检索到的来源")

    # 检查 3: 对比类问题内容不充分
    if complexity == "comparison" and len(answer) < 250:
        issues.append("对比分析内容不够充分")

    # 检查 4: 有已知关系但未提及
    relations = state.get("known_relations", [])
    if relations and not any(
        kw in answer for kw in ["矛盾", "互补", "延伸", "重叠", "contradict", "complement"]
    ):
        issues.append("未涉及已知的论文间关系")

    if issues:
        # 判断是需要补数据还是只需要改回答
        needs_data = any(
            kw in " ".join(issues)
            for kw in ["检索", "来源", "不足"]
        )
        return {
            "pass": False,
            "issues": issues,
            "suggestion": "请补充上述不足",
            "needs_data": needs_data,
        }

    return {"pass": True, "issues": [], "suggestion": "", "needs_data": False}


# ================================================================
# 研究状态更新（定稿后才写入）
# ================================================================

RESEARCH_STATE_PROMPT = """从以下研究问答中提取用户的研究状态。

提取以下类型：
- question: 用户关注的研究问题
- hypothesis: 待验证的假设
- conclusion: 已确认的结论
- direction: 后续研究方向

只输出 JSON（不要任何别的文字）：
{"items": [{"type": "question", "content": "..."}]}

如果信息不足以提取，输出 {"items": []}"""


async def extract_and_save_research_state(
    user_id: str, query: str, answer: str, session_id: str = "",
):
    """
    从最终定稿中提取研究状态，写入 DB（功能 C 的写入端）
    只在 evaluation.pass = True 后才调用。
    """
    from backend.llm_adapters.base import resolve_adapter
    from backend.config import get_settings
    from backend.services.research_state_service import save_research_items

    settings = get_settings()

    try:
        try:
            adapter, m = resolve_adapter(settings.light_llm_model)
        except ValueError:
            adapter, m = resolve_adapter(settings.default_llm_model)

        content = f"用户问题：{query}\n\n回答：{answer[:1500]}"
        resp = await adapter.chat(
            model=m,
            messages=[{"role": "user", "content": content}],
            system=RESEARCH_STATE_PROMPT,
            temperature=0.2, max_tokens=500,
        )

        match = re.search(r'\{.*\}', resp, re.DOTALL)
        if match:
            data = json.loads(match.group())
            items = data.get("items", [])
            if items:
                save_research_items(user_id, items, session_id)
                logger.info(f"Saved {len(items)} research state items")
    except Exception as e:
        logger.warning(f"Research state extraction failed: {e}")


# ================================================================
# 核心 SSE 流式接口
# ================================================================

@router.post("/stream")
async def research_stream(req: ResearchRequest):
    """
    深度研究 SSE 流式接口

    外层 Replan 循环：
      Phase 1: LangGraph 数据收集
      Phase 2: 流式 LLM 生成
      Phase 3: 质量检查
      Phase 4: 通过 → done / 不通过 → replan
    """
    from backend.config import get_settings
    from backend.graph.graph import get_research_graph, build_initial_state

    settings = get_settings()
    model = req.model or settings.default_llm_model
    session_id = req.session_id or str(uuid.uuid4())

    # 验证模型
    try:
        from backend.llm_adapters.base import resolve_adapter
        resolve_adapter(model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    async def event_generator():
        graph = get_research_graph()
        state = build_initial_state(
            query=req.query,
            user_id=MOCK_USER_ID,
            model=model,
        )

        replan_count = 0
        evaluation = None

        try:
            while replan_count <= 2:
                # ========================================
                # Phase 1: LangGraph 数据收集
                # ========================================
                need_graph = (
                    replan_count == 0  # 首次一定要跑
                    or (evaluation and evaluation.get("needs_data", False))
                )

                if need_graph:
                    if replan_count > 0 and evaluation:
                        # 注入 replan 信息
                        state["_replan_issues"] = evaluation.get("issues", [])
                        # 重新构建图的部分状态
                        from backend.graph.nodes import planner_replan_node
                        replan_result = await planner_replan_node(state)
                        replan_logs = replan_result.pop("step_logs", [])
                        if replan_logs:
                            state.setdefault("step_logs", []).extend(replan_logs)
                        state.update(replan_result)

                        # 发送 replan 事件
                        yield _sse({"type": "replan",
                                    "reason": "; ".join(evaluation.get("issues", [])),
                                    "count": replan_count})

                    try:
                        async for event in graph.astream(state, stream_mode="updates"):
                            for node_name, updates in event.items():
                                # 推送日志
                                for log in updates.get("step_logs", []):
                                    yield _sse({"type": "log", "message": log})

                                # 推送计划（首次规划）
                                if node_name == "classify_and_plan" and "plan" in updates:
                                    yield _sse({
                                        "type": "plan",
                                        "steps": updates["plan"],
                                        "complexity": updates.get("complexity", ""),
                                    })

                                # 推送步骤进度
                                if node_name == "executor" and "plan" in updates:
                                    for s in updates["plan"]:
                                        if s["status"] in ("done", "failed"):
                                            yield _sse({
                                                "type": "progress",
                                                "step_id": s["id"],
                                                "action": s["action"],
                                                "status": s["status"],
                                                "summary": s["result_summary"],
                                            })

                                # 推送论文关系
                                if "known_relations" in updates and updates["known_relations"]:
                                    yield _sse({
                                        "type": "relation",
                                        "data": updates["known_relations"],
                                    })

                                # 推送研究状态
                                if "user_research_ctx" in updates and updates["user_research_ctx"]:
                                    yield _sse({
                                        "type": "research_ctx",
                                        "data": updates["user_research_ctx"],
                                    })

                                # 更新 state（step_logs 需要追加而非覆盖）
                                new_logs = updates.pop("step_logs", [])
                                if new_logs:
                                    state.setdefault("step_logs", []).extend(new_logs)
                                state.update(updates)

                    except Exception as e:
                        logger.error(f"Graph execution failed: {e}", exc_info=True)
                        yield _sse({"type": "error", "message": f"数据收集失败：{str(e)[:200]}"})
                        yield _sse({"type": "done"})
                        return

                # ========================================
                # Phase 2: 流式 LLM 生成回答
                # ========================================
                yield _sse({"type": "log", "message": "✍️ Synthesizer: 开始生成回答..."})

                answer_chunks = []
                try:
                    async for token in synthesize_stream(state, evaluation):
                        answer_chunks.append(token)
                        yield _sse({"type": "token", "content": token})
                except Exception as e:
                    error_msg = f"生成失败：{str(e)[:200]}"
                    yield _sse({"type": "token", "content": error_msg})
                    answer_chunks.append(error_msg)

                full_answer = "".join(answer_chunks)

                # ========================================
                # Phase 3: 质量检查
                # ========================================
                evaluation = evaluate_answer(full_answer, state)

                if evaluation["pass"]:
                    # ========================================
                    # Phase 4a: 通过 → 定稿
                    # ========================================
                    yield _sse({"type": "log", "message": "✅ Evaluator: 质量检查通过"})

                    # 异步写入研究状态（不阻塞）
                    import asyncio
                    asyncio.create_task(
                        extract_and_save_research_state(
                            MOCK_USER_ID, req.query, full_answer, session_id
                        )
                    )

                    yield _sse({"type": "done"})
                    return

                else:
                    # ========================================
                    # Phase 4b: 未通过 → Replan
                    # ========================================
                    replan_count += 1
                    if replan_count > 2:
                        # 重规划次数耗尽，用现有结果
                        yield _sse({"type": "log",
                                    "message": "⚠️ Evaluator: 重规划次数耗尽，使用当前结果"})
                        yield _sse({"type": "done"})
                        return

                    issues_str = "; ".join(evaluation.get("issues", []))
                    yield _sse({"type": "log",
                                "message": f"⚠️ Evaluator: {issues_str} → 触发重规划"})

                    # 如果不需要补数据，直接回到 Phase 2
                    if not evaluation.get("needs_data", False):
                        yield _sse({"type": "replan",
                                    "reason": issues_str, "count": replan_count})
                        continue

                    # 需要补数据，回到 Phase 1

            # 循环结束（不应到达这里）
            yield _sse({"type": "done"})

        except Exception as e:
            logger.error(f"Research stream error: {e}", exc_info=True)
            yield _sse({"type": "error", "message": str(e)[:300]})
            yield _sse({"type": "done"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ================================================================
# 非流式接口（兼容/测试用）
# ================================================================

@router.post("/run")
async def research_run(req: ResearchRequest):
    """非流式版本，返回完整结果"""
    from backend.config import get_settings
    from backend.graph.graph import get_research_graph, build_initial_state
    from backend.llm_adapters.base import resolve_adapter

    settings = get_settings()
    model = req.model or settings.default_llm_model
    session_id = req.session_id or str(uuid.uuid4())

    graph = get_research_graph()
    state = build_initial_state(req.query, MOCK_USER_ID, model)

    # 运行图
    try:
        final_state = await graph.ainvoke(state)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Graph failed: {e}")

    # 生成回答
    adapter, model_name = resolve_adapter(model)
    context = _build_synth_context(final_state)
    system = SYNTH_SYSTEM + "\n\n" + context

    try:
        answer = await adapter.chat(
            model=model_name,
            messages=[{"role": "user", "content": req.query}],
            system=system,
            temperature=0.5, max_tokens=3000,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM failed: {e}")

    # 评估
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
        "sources": [
            {
                "type": r.get("type", "paper"),
                "section_title": r.get("section_title", "") or r.get("title", ""),
                "content": r.get("content", "")[:300],
                "score": r.get("score", 0),
            }
            for r in final_state.get("search_results", [])
            if r.get("score", 0) >= 0.2
        ],
    }


# ================================================================
# Dashboard API
# ================================================================

@router.get("/dashboard")
async def research_dashboard():
    """研究看板数据"""
    from backend.services.research_state_service import get_all_items, get_unread_notifications
    from backend.services.relations_service import get_all_relations

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


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
