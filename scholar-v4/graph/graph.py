"""
ScholarAgent LangGraph 图定义

图的职责：纯数据收集管线
  classify_and_plan → executor → checkpoint → executor (循环)
                                     ↓
                                    END

Synthesizer 和 Evaluator 不在图中，由外层 SSE 接口处理。
这样做的好处：
  1. 回答可以真正流式输出（逐 token）
  2. 图的逻辑更纯粹（只做数据收集和路由）
  3. 避免在 LangGraph 节点内做流式输出的技术难题

Replan 循环：
  外层发现需要补数据 → 注入新步骤到 state → 重新运行图
  外层发现只需要改回答 → 不重新运行图，直接重新生成
"""

import logging
from langgraph.graph import StateGraph, END

from backend.graph.state import ResearchState
from backend.graph.nodes import (
    classify_and_plan_node,
    executor_node,
    checkpoint_node,
)

logger = logging.getLogger(__name__)


def _route_checkpoint(state: dict) -> str:
    """checkpoint 后的条件路由"""
    action = state.get("next_action", "finish")
    if action == "execute":
        return "executor"
    return END


def build_research_graph():
    """
    构建 LangGraph 数据收集图

    图结构：
      classify_and_plan → executor → checkpoint → executor (pending)
                                              → END (all done)
    """
    g = StateGraph(ResearchState)

    # 添加节点
    g.add_node("classify_and_plan", classify_and_plan_node)
    g.add_node("executor", executor_node)
    g.add_node("checkpoint", checkpoint_node)

    # 设置入口
    g.set_entry_point("classify_and_plan")

    # 添加边
    g.add_edge("classify_and_plan", "executor")
    g.add_edge("executor", "checkpoint")

    # checkpoint 条件路由
    g.add_conditional_edges(
        "checkpoint",
        _route_checkpoint,
        {
            "executor": "executor",
            END: END,
        },
    )

    return g.compile()


# 全局编译（复用）
_compiled = None


def get_research_graph():
    global _compiled
    if _compiled is None:
        _compiled = build_research_graph()
        logger.info("Research graph compiled successfully")
    return _compiled


def build_initial_state(
    query: str,
    user_id: str,
    model: str,
) -> dict:
    """构建图的初始状态"""
    return {
        "query": query,
        "user_id": user_id,
        "model": model,
        "complexity": "",
        "plan": [],
        "current_step_idx": 0,
        "replan_count": 0,
        "search_results": [],
        "known_relations": [],
        "user_research_ctx": [],
        "step_logs": [],
        "next_action": "",
        "error_msg": "",
    }
