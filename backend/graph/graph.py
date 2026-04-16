"""
Research graph definition.
"""

import logging

from langgraph.graph import END, StateGraph

from backend.graph.nodes import (
    classify_and_plan_node,
    checkpoint_node,
    executor_node,
)
from backend.graph.state import ResearchState

logger = logging.getLogger(__name__)


def _route_checkpoint(state: dict) -> str:
    return "executor" if state.get("next_action", "finish") == "execute" else END


def build_research_graph():
    graph = StateGraph(ResearchState)
    graph.add_node("classify_and_plan", classify_and_plan_node)
    graph.add_node("executor", executor_node)
    graph.add_node("checkpoint", checkpoint_node)

    graph.set_entry_point("classify_and_plan")
    graph.add_edge("classify_and_plan", "executor")
    graph.add_edge("executor", "checkpoint")
    graph.add_conditional_edges("checkpoint", _route_checkpoint, {"executor": "executor", END: END})

    return graph.compile()


_compiled = None


def get_research_graph():
    global _compiled
    if _compiled is None:
        _compiled = build_research_graph()
        logger.info("Research graph compiled successfully")
    return _compiled


def build_initial_state(query: str, user_id: str, model: str) -> dict:
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
