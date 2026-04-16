"""
ScholarAgent LangGraph 状态定义

State 在图的所有节点之间共享和流转。
图只负责数据收集（Researcher 工具调用），
Synthesizer 和 Evaluator 在图外的 SSE 层处理。
"""

from typing import TypedDict, Annotated
from operator import add


class PlanStep(TypedDict):
    """计划中的一个步骤"""
    id: int
    action: str          # retrieve / lookup_relations / lookup_state
    params: dict         # 动作参数
    status: str          # pending / running / done / failed
    result_summary: str  # 执行结果摘要


class ResearchState(TypedDict):
    """
    共享状态

    设计原则：
    - search_results, known_relations, user_research_ctx 是结构化数据（工具调用产出）
    - step_logs 是唯一使用 Annotated[list, add] 的字段（追加模式）
    - 其他字段都是整体替换语义
    """

    # ── 输入 ──
    query: str
    user_id: str
    model: str
    complexity: str              # simple / analysis / comparison / exploratory

    # ── 计划 ──
    plan: list[PlanStep]
    current_step_idx: int
    replan_count: int

    # ── Researcher 产出（结构化数据，非 LLM 文本）──
    search_results: list[dict]     # 向量检索结果
    known_relations: list[dict]    # 从 paper_relations 表查到的
    user_research_ctx: list[dict]  # 从 research_state 表查到的

    # ── 流程控制 ──
    step_logs: Annotated[list[str], add]
    next_action: str               # execute / finish / error
    error_msg: str
