"""
Research graph shared state.
"""

from operator import add
from typing import Annotated, TypedDict


class PlanStep(TypedDict):
    id: int
    action: str
    params: dict
    status: str
    result_summary: str


class ResearchState(TypedDict):
    query: str
    user_id: str
    model: str
    complexity: str
    plan: list[PlanStep]
    current_step_idx: int
    replan_count: int
    search_results: list[dict]
    known_relations: list[dict]
    user_research_ctx: list[dict]
    step_logs: Annotated[list[str], add]
    next_action: str
    error_msg: str
