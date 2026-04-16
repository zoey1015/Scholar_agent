"""
ScholarAgent LangGraph 节点

节点职责：
  classify_and_plan:   规则复杂度分类 + 计划生成（0ms，极少调 LLM）
  executor:            根据 step.action 路由到 Researcher 的工具函数
  checkpoint:          检查是否还有 pending 步骤
  planner_replan:      基于评估反馈生成补充步骤（规则驱动）

Researcher 工具函数（在 executor 中调用）：
  researcher_retrieve:          向量检索（Milvus）
  researcher_lookup_relations:  查预计算关系（PostgreSQL）
  researcher_lookup_state:      查用户研究状态（PostgreSQL）

注意：本文件中没有 LLM 生成（面向用户的文本）调用。
Researcher 的三个工具函数完全不调 LLM。
仅有两处辅助性 LLM 调用：query 中英文重写（带超时）、exploratory 类规划（极少触发）。
Synthesizer 和 Evaluator 不在图中，由外层 SSE 接口处理。
"""

import re
import json
import logging
import asyncio

logger = logging.getLogger(__name__)

MOCK_USER_ID = "00000000-0000-0000-0000-000000000001"

# ================================================================
# 复杂度分类规则
# ================================================================

COMPLEXITY_KEYWORDS = [
    # (关键词集合, 复杂度等级)
    ({"对比", "比较", "区别", "矛盾", "互补", "compare", "contrast", "vs",
      "调研", "研究空白", "系统", "research gap", "综述", "全面"}, "comparison"),
    ({"分析", "现状", "趋势", "创新点", "方法论", "analyze", "survey", "review",
      "总结", "归纳", "优劣"}, "analysis"),
]

# ================================================================
# 计划模板（规则生成，0ms）
# ================================================================

PLAN_TEMPLATES = {
    "simple": [
        {"action": "retrieve", "params": {}},
    ],
    "analysis": [
        {"action": "retrieve", "params": {}},
        {"action": "lookup_relations", "params": {}},
    ],
    "comparison": [
        {"action": "retrieve", "params": {}},
        {"action": "lookup_relations", "params": {}},
        {"action": "lookup_state", "params": {}},
    ],
}


# ================================================================
# Node: classify_and_plan（Planner 入口）
# ================================================================

async def classify_and_plan_node(state: dict) -> dict:
    """
    复杂度分类 + 计划生成

    90%+ 场景用规则（0ms），不调 LLM。
    只有无法归类时才调 LLM（exploratory）。

    Replan 场景：如果 plan 中已有 pending 步骤（由外层 planner_replan_node 注入），
    跳过重新规划，直接沿用。
    """
    # Replan 快捷路径：外层已经注入了新步骤
    existing_plan = state.get("plan", [])
    if existing_plan and any(s["status"] == "pending" for s in existing_plan):
        return {
            "step_logs": [f"🧠 Planner: 沿用 replan 计划（{len(existing_plan)} 步）"],
            "next_action": "execute",
        }

    query = state["query"]
    q_lower = query.lower()

    # 规则分类
    complexity = "simple"
    for keywords, level in COMPLEXITY_KEYWORDS:
        if any(kw in q_lower for kw in keywords):
            complexity = level
            break

    # 生成计划
    if complexity in PLAN_TEMPLATES:
        raw_steps = PLAN_TEMPLATES[complexity]
        log = f"🧠 Planner: 规则分类 [{complexity}]，{len(raw_steps)} 步计划"
    else:
        # exploratory: 调 LLM 生成计划（极少触发）
        raw_steps = await _llm_generate_plan(query, state["model"])
        complexity = "exploratory"
        log = f"🧠 Planner: LLM 规划 [{complexity}]，{len(raw_steps)} 步计划"

    # 构建 PlanStep 列表
    plan = []
    for i, s in enumerate(raw_steps):
        plan.append({
            "id": i + 1,
            "action": s["action"],
            "params": s.get("params", {}),
            "status": "pending",
            "result_summary": "",
        })

    return {
        "complexity": complexity,
        "plan": plan,
        "current_step_idx": 0,
        "step_logs": [log],
        "next_action": "execute",
    }


async def _llm_generate_plan(query: str, model: str) -> list[dict]:
    """LLM 生成计划（仅 exploratory 时调用）"""
    try:
        from backend.llm_adapters.base import resolve_adapter
        from backend.config import get_settings
        settings = get_settings()

        try:
            adapter, m = resolve_adapter(settings.light_llm_model)
        except ValueError:
            adapter, m = resolve_adapter(model)

        prompt = (
            "你是研究规划器。用户问题如下，请生成研究步骤。\n"
            "可用 action: retrieve（检索）, lookup_relations（查论文关系）, lookup_state（查研究状态）\n"
            "只输出 JSON：{\"steps\": [{\"action\": \"...\"}]}\n\n"
            f"问题：{query}"
        )
        resp = await adapter.chat(
            model=m,
            messages=[{"role": "user", "content": prompt}],
            system="",
            temperature=0.2, max_tokens=300,
        )

        match = re.search(r'\{.*\}', resp, re.DOTALL)
        if match:
            data = json.loads(match.group())
            steps = data.get("steps", [])
            # 过滤掉不合法的 action
            valid = {"retrieve", "lookup_relations", "lookup_state"}
            steps = [s for s in steps if s.get("action") in valid]
            if steps:
                return steps
    except Exception as e:
        logger.warning(f"LLM plan generation failed: {e}")

    # 兜底：默认计划
    return [{"action": "retrieve", "params": {}}]


# ================================================================
# Node: executor（路由到 Researcher 工具）
# ================================================================

async def executor_node(state: dict) -> dict:
    """
    执行器：找到第一个 pending 步骤，路由到对应的 Researcher 工具函数。
    executor 本身不做任何业务逻辑。
    """
    plan = list(state.get("plan", []))

    # 找第一个 pending
    current = None
    for s in plan:
        if s["status"] == "pending":
            current = s
            break

    if not current:
        return {"plan": plan, "next_action": "execute"}

    current["status"] = "running"
    action = current["action"]
    params = current.get("params", {})

    handler = _ACTION_HANDLERS.get(action)
    if not handler:
        current["status"] = "failed"
        current["result_summary"] = f"未知 action: {action}"
        return {
            "plan": plan,
            "step_logs": [f"❌ 未知操作: {action}"],
        }

    try:
        result = await handler(state, params)
        current["status"] = "done"
        current["result_summary"] = result.pop("_summary", "完成")
        result["plan"] = plan
        return result
    except Exception as e:
        current["status"] = "failed"
        current["result_summary"] = str(e)[:200]
        return {
            "plan": plan,
            "step_logs": [f"❌ {action} 失败: {str(e)[:100]}"],
            "error_msg": str(e),
        }


# ================================================================
# Node: checkpoint
# ================================================================

async def checkpoint_node(state: dict) -> dict:
    """检查是否还有 pending 步骤"""
    plan = state.get("plan", [])
    has_pending = any(s["status"] == "pending" for s in plan)

    if has_pending:
        return {"next_action": "execute"}
    else:
        return {"next_action": "finish"}


# ================================================================
# Node: planner_replan（由外层 SSE 接口调用时注入新步骤后重启图）
# ================================================================

async def planner_replan_node(state: dict) -> dict:
    """
    重规划：基于外层 Evaluator 的结构化反馈，生成针对性的补充步骤。
    规则驱动（0ms），不调 LLM。
    """
    issues = state.get("_replan_issues", [])
    replan_count = state.get("replan_count", 0)

    new_steps = []

    for issue in issues:
        issue_lower = issue.lower()
        if any(kw in issue_lower for kw in ["检索", "来源", "不足", "retrieve", "source"]):
            new_steps.append({
                "action": "retrieve",
                "params": {"strategy": "expand"},
            })
        if any(kw in issue_lower for kw in ["对比", "关系", "relation", "多角度"]):
            new_steps.append({
                "action": "lookup_relations",
                "params": {},
            })

    # 去重：不重复已经 done 的 action
    done_actions = {s["action"] for s in state.get("plan", []) if s["status"] == "done"}
    # 只保留 retrieve 的扩展（因为 lookup 类查一次就够了）
    new_steps = [
        s for s in new_steps
        if s["action"] == "retrieve" or s["action"] not in done_actions
    ]

    if not new_steps:
        # 没有需要补充的数据步骤
        return {
            "step_logs": [f"🔄 Planner: 无需补充数据步骤"],
            "next_action": "finish",
        }

    # 构建新 plan（保留已完成的 + 新增的）
    existing_done = [s for s in state.get("plan", []) if s["status"] == "done"]
    start_id = len(existing_done) + 1

    plan_additions = []
    for i, s in enumerate(new_steps):
        plan_additions.append({
            "id": start_id + i,
            "action": s["action"],
            "params": s.get("params", {}),
            "status": "pending",
            "result_summary": "",
        })

    full_plan = existing_done + plan_additions

    return {
        "plan": full_plan,
        "current_step_idx": 0,
        "replan_count": replan_count + 1,
        "step_logs": [f"🔄 Planner: 重规划第 {replan_count + 1} 次，新增 {len(plan_additions)} 步"],
        "next_action": "execute",
    }


# ================================================================
# Researcher 工具函数
# ================================================================

async def researcher_retrieve(state: dict, params: dict) -> dict:
    """
    工具 1: 向量检索（Milvus）

    这是工具调用，不是 LLM 调用。
    如果有中文 query，会带 3 秒超时做中→英重写。
    """
    query = state["query"]
    user_id = state["user_id"]
    strategy = params.get("strategy", "default")

    queries = [query]

    # 中文→英文重写（带超时，失败不阻塞）
    if _has_chinese(query):
        en_queries = await _rewrite_with_timeout(query, state["model"], timeout=3.0)
        queries.extend(en_queries)

    # 如果是扩展检索策略，尝试提取额外关键词
    if strategy == "expand" and len(queries) < 3:
        # 从已有检索结果中提取高频术语作为补充 query
        existing = state.get("search_results", [])
        extra_kw = _extract_keywords_from_results(existing)
        if extra_kw:
            queries.append(extra_kw)

    # 并发检索论文和笔记
    papers, notes = await asyncio.gather(
        _search_papers(queries, user_id, top_k=8),
        _search_notes(query, user_id, top_k=3),
    )

    # 合并（去重）
    all_results = _merge_results(
        state.get("search_results", []),
        papers + notes,
    )

    count = len(all_results)
    return {
        "search_results": all_results,
        "step_logs": [f"🔍 Researcher: 检索到 {len(papers)} 篇论文 + {len(notes)} 条笔记"],
        "_summary": f"共 {count} 条结果",
    }


async def researcher_lookup_relations(state: dict, params: dict) -> dict:
    """
    工具 2: 查询预计算的论文关系（PostgreSQL，~0.1 秒）

    不调 LLM。直接查 paper_relations 表。
    """
    from backend.services.relations_service import get_relations_for_documents

    # 从检索结果中提取 document_id
    doc_ids = set()
    for r in state.get("search_results", []):
        did = r.get("document_id")
        if did:
            doc_ids.add(did)

    if not doc_ids:
        return {
            "known_relations": [],
            "step_logs": ["⚖️ Researcher: 无文档 ID，跳过关系查询"],
            "_summary": "无关系数据",
        }

    relations = get_relations_for_documents(list(doc_ids))

    return {
        "known_relations": relations,
        "step_logs": [f"⚖️ Researcher: 查到 {len(relations)} 组预计算关系"],
        "_summary": f"{len(relations)} 组论文关系",
    }


async def researcher_lookup_state(state: dict, params: dict) -> dict:
    """
    工具 3: 查询用户研究状态（PostgreSQL，~0.1 秒）

    不调 LLM。直接查 research_state 表。
    """
    from backend.services.research_state_service import get_open_items

    items = get_open_items(state["user_id"])

    return {
        "user_research_ctx": items,
        "step_logs": [f"📊 Researcher: 加载 {len(items)} 条研究状态"],
        "_summary": f"{len(items)} 条研究状态",
    }


# Action → Handler 映射
_ACTION_HANDLERS = {
    "retrieve": researcher_retrieve,
    "lookup_relations": researcher_lookup_relations,
    "lookup_state": researcher_lookup_state,
}


# ================================================================
# 辅助函数
# ================================================================

def _has_chinese(text: str) -> bool:
    return any('\u4e00' <= c <= '\u9fff' for c in text)


async def _rewrite_with_timeout(query: str, model: str, timeout: float = 3.0) -> list[str]:
    """中→英 query 重写，带超时"""
    try:
        return await asyncio.wait_for(_do_rewrite(query, model), timeout=timeout)
    except (asyncio.TimeoutError, Exception) as e:
        logger.debug(f"Rewrite skipped: {e}")
        return []


async def _do_rewrite(query: str, model: str) -> list[str]:
    from backend.llm_adapters.base import resolve_adapter
    from backend.config import get_settings
    settings = get_settings()

    try:
        adapter, m = resolve_adapter(settings.light_llm_model)
    except ValueError:
        adapter, m = resolve_adapter(model)

    resp = await adapter.chat(
        model=m,
        messages=[{"role": "user", "content":
            f"把以下中文学术查询翻译为2个英文检索短语，只输出JSON：{{\"q\":[\"...\"]}}。查询：{query}"}],
        system="", temperature=0.1, max_tokens=150,
    )

    match = re.search(r'\{.*\}', resp, re.DOTALL)
    if match:
        data = json.loads(match.group())
        result = data.get("q", [])
        if result:
            logger.info(f"Rewritten: '{query}' → {result}")
            return result
    return []


async def _search_papers(queries: list[str], user_id: str, top_k: int = 8) -> list[dict]:
    """向量检索论文"""
    from backend.skills.base import SkillContext
    from backend.skills.registry import skill_registry

    skill = skill_registry.get("retrieval")
    if not skill:
        return []

    all_results = []
    seen = set()

    for q in queries[:3]:
        ctx = SkillContext(
            user_id=user_id, query=q,
            metadata={"top_k": top_k, "doc_type": "all"},
        )
        try:
            r = await skill.execute(ctx)
            if r.data:
                for item in r.data.get("results", []):
                    cid = item.get("chunk_id", id(item))
                    if cid not in seen:
                        seen.add(cid)
                        all_results.append(item)
        except Exception as e:
            logger.warning(f"Paper search failed for '{q}': {e}")

    all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return all_results[:top_k]


async def _search_notes(query: str, user_id: str, top_k: int = 3) -> list[dict]:
    """检索笔记"""
    try:
        from backend.services.notes_service import get_notes_service
        from concurrent.futures import ThreadPoolExecutor

        def _s():
            return get_notes_service().search_notes(user_id, query, top_k)

        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor() as pool:
            results = await loop.run_in_executor(pool, _s)

        # 统一格式
        return [
            {
                "type": "note",
                "title": n.get("title", ""),
                "content": n.get("summary", ""),
                "score": n.get("score", 0),
                "note_id": n.get("note_id", ""),
            }
            for n in results
        ]
    except Exception as e:
        logger.debug(f"Note search failed: {e}")
        return []


def _merge_results(existing: list[dict], new: list[dict]) -> list[dict]:
    """合并检索结果，去重"""
    seen = set()
    merged = []

    for item in existing + new:
        key = item.get("chunk_id") or item.get("note_id") or item.get("content", "")[:50]
        if key not in seen:
            seen.add(key)
            merged.append(item)

    merged.sort(key=lambda x: x.get("score", 0), reverse=True)
    return merged[:15]  # 限制总量


def _extract_keywords_from_results(results: list[dict]) -> str:
    """从已有检索结果中提取高频关键词（用于扩展检索）"""
    if not results:
        return ""

    # 简单策略：取第一个结果的 section_title
    for r in results[:3]:
        section = r.get("section_title", "")
        if section and len(section) > 3:
            return section

    return ""
