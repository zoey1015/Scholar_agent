"""
Research graph nodes.
"""

import asyncio
import json
import logging
import re

logger = logging.getLogger(__name__)


COMPLEXITY_KEYWORDS = [
    ({"对比", "比较", "区别", "矛盾", "互补", "compare", "contrast", "vs", "调研", "研究空白", "系统", "research gap", "综述", "全面"}, "comparison"),
    ({"分析", "现状", "趋势", "创新点", "方法论", "analyze", "survey", "review", "总结", "归纳", "优劣"}, "analysis"),
]

PLAN_TEMPLATES = {
    "simple": [{"action": "retrieve", "params": {}}],
    "analysis": [{"action": "retrieve", "params": {}}, {"action": "lookup_relations", "params": {}}],
    "comparison": [
        {"action": "retrieve", "params": {}},
        {"action": "lookup_relations", "params": {}},
        {"action": "lookup_state", "params": {}},
    ],
}


async def classify_and_plan_node(state: dict) -> dict:
    existing_plan = state.get("plan", [])
    if existing_plan and any(step.get("status") == "pending" for step in existing_plan):
        return {
            "step_logs": [f"🧠 Planner: 沿用 replan 计划（{len(existing_plan)} 步）"],
            "next_action": "execute",
        }

    query = state["query"]
    q_lower = query.lower()

    complexity = "simple"
    for keywords, level in COMPLEXITY_KEYWORDS:
        if any(keyword in q_lower for keyword in keywords):
            complexity = level
            break

    if complexity in PLAN_TEMPLATES:
        raw_steps = PLAN_TEMPLATES[complexity]
        log = f"🧠 Planner: 规则分类 [{complexity}]，{len(raw_steps)} 步计划"
    else:
        raw_steps = await _llm_generate_plan(query, state["model"])
        complexity = "exploratory"
        log = f"🧠 Planner: LLM 规划 [{complexity}]，{len(raw_steps)} 步计划"

    plan = []
    for idx, step in enumerate(raw_steps):
        plan.append(
            {
                "id": idx + 1,
                "action": step["action"],
                "params": step.get("params", {}),
                "status": "pending",
                "result_summary": "",
            }
        )

    return {
        "complexity": complexity,
        "plan": plan,
        "current_step_idx": 0,
        "step_logs": [log],
        "next_action": "execute",
    }


async def _llm_generate_plan(query: str, model: str) -> list[dict]:
    try:
        from backend.config import get_settings
        from backend.llm_adapters.base import resolve_adapter

        settings = get_settings()
        try:
            adapter, model_name = resolve_adapter(settings.light_llm_model)
        except ValueError:
            adapter, model_name = resolve_adapter(model)

        prompt = (
            "你是研究规划器。用户问题如下，请生成研究步骤。\n"
            "可用 action: retrieve、lookup_relations、lookup_state\n"
            "只输出 JSON：{\"steps\": [{\"action\": \"...\"}]}\n\n"
            f"问题：{query}"
        )
        resp = await adapter.chat(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            system="",
            temperature=0.2,
            max_tokens=300,
        )

        match = re.search(r"\{.*\}", resp, re.DOTALL)
        if match:
            data = json.loads(match.group())
            steps = data.get("steps", [])
            valid = {"retrieve", "lookup_relations", "lookup_state"}
            steps = [step for step in steps if step.get("action") in valid]
            if steps:
                return steps
    except Exception as exc:
        logger.warning(f"LLM plan generation failed: {exc}")

    return [{"action": "retrieve", "params": {}}]


async def executor_node(state: dict) -> dict:
    plan = list(state.get("plan", []))

    current = None
    for step in plan:
        if step.get("status") == "pending":
            current = step
            break

    if not current:
        return {"plan": plan, "next_action": "execute"}

    current["status"] = "running"
    handler = _ACTION_HANDLERS.get(current["action"])
    if not handler:
        current["status"] = "failed"
        current["result_summary"] = f"未知 action: {current['action']}"
        return {"plan": plan, "step_logs": [f"❌ 未知操作: {current['action']}"]}

    try:
        result = await handler(state, current.get("params", {}))
        current["status"] = "done"
        current["result_summary"] = result.pop("_summary", "完成")
        result["plan"] = plan
        return result
    except Exception as exc:
        current["status"] = "failed"
        current["result_summary"] = str(exc)[:200]
        return {"plan": plan, "step_logs": [f"❌ {current['action']} 失败: {str(exc)[:100]}"] , "error_msg": str(exc)}


async def checkpoint_node(state: dict) -> dict:
    plan = state.get("plan", [])
    return {"next_action": "execute" if any(step.get("status") == "pending" for step in plan) else "finish"}


async def planner_replan_node(state: dict) -> dict:
    issues = state.get("_replan_issues", [])
    replan_count = state.get("replan_count", 0)

    new_steps = []
    for issue in issues:
        issue_lower = issue.lower()
        if any(keyword in issue_lower for keyword in ["检索", "来源", "不足", "retrieve", "source"]):
            new_steps.append({"action": "retrieve", "params": {"strategy": "expand"}})
        if any(keyword in issue_lower for keyword in ["对比", "关系", "relation", "多角度"]):
            new_steps.append({"action": "lookup_relations", "params": {}})

    done_actions = {step["action"] for step in state.get("plan", []) if step.get("status") == "done"}
    new_steps = [step for step in new_steps if step["action"] == "retrieve" or step["action"] not in done_actions]

    if not new_steps:
        return {"step_logs": ["🔄 Planner: 无需补充数据步骤"], "next_action": "finish"}

    existing_done = [step for step in state.get("plan", []) if step.get("status") == "done"]
    start_id = len(existing_done) + 1
    plan_additions = []
    for idx, step in enumerate(new_steps):
        plan_additions.append(
            {
                "id": start_id + idx,
                "action": step["action"],
                "params": step.get("params", {}),
                "status": "pending",
                "result_summary": "",
            }
        )

    return {
        "plan": existing_done + plan_additions,
        "current_step_idx": 0,
        "replan_count": replan_count + 1,
        "step_logs": [f"🔄 Planner: 重规划第 {replan_count + 1} 次，新增 {len(plan_additions)} 步"],
        "next_action": "execute",
    }


async def researcher_retrieve(state: dict, params: dict) -> dict:
    query = state["query"]
    user_id = state["user_id"]
    strategy = params.get("strategy", "default")

    queries = [query]
    if _has_chinese(query):
        queries.extend(await _rewrite_with_timeout(query, state["model"], timeout=3.0))

    if strategy == "expand" and len(queries) < 3:
        extra_kw = _extract_keywords_from_results(state.get("search_results", []))
        if extra_kw:
            queries.append(extra_kw)

    papers, notes = await asyncio.gather(
        _search_papers(queries, user_id, top_k=8),
        _search_notes(query, user_id, top_k=3),
    )

    all_results = _merge_results(state.get("search_results", []), papers + notes)
    return {
        "search_results": all_results,
        "step_logs": [f"🔍 Researcher: 检索到 {len(papers)} 篇论文 + {len(notes)} 条笔记"],
        "_summary": f"共 {len(all_results)} 条结果",
    }


async def researcher_lookup_relations(state: dict, params: dict) -> dict:
    from backend.services.relations_service import get_relations_for_documents

    doc_ids = {result.get("document_id") for result in state.get("search_results", []) if result.get("document_id")}
    if not doc_ids:
        return {"known_relations": [], "step_logs": ["⚖️ Researcher: 无文档 ID，跳过关系查询"], "_summary": "无关系数据"}

    relations = get_relations_for_documents(list(doc_ids))
    return {
        "known_relations": relations,
        "step_logs": [f"⚖️ Researcher: 查到 {len(relations)} 组预计算关系"],
        "_summary": f"{len(relations)} 组论文关系",
    }


async def researcher_lookup_state(state: dict, params: dict) -> dict:
    from backend.services.research_state_service import get_open_items

    items = get_open_items(state["user_id"])
    return {"user_research_ctx": items, "step_logs": [f"📊 Researcher: 加载 {len(items)} 条研究状态记录"], "_summary": f"{len(items)} 条研究状态"}


_ACTION_HANDLERS = {
    "retrieve": researcher_retrieve,
    "lookup_relations": researcher_lookup_relations,
    "lookup_state": researcher_lookup_state,
}


def _has_chinese(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


async def _rewrite_with_timeout(query: str, model: str, timeout: float = 3.0) -> list[str]:
    try:
        return (await asyncio.wait_for(_rewrite_query(query, model), timeout=timeout)).get("en_queries", [query])
    except Exception:
        return [query]


async def _rewrite_query(query: str, model: str) -> dict:
    from backend.config import get_settings
    from backend.llm_adapters.base import resolve_adapter

    settings = get_settings()
    try:
        adapter, model_name = resolve_adapter(settings.light_llm_model)
    except ValueError:
        adapter, model_name = resolve_adapter(model)

    response = await adapter.chat(
        model=model_name,
        messages=[{"role": "user", "content": f"用户查询：{query}"}],
        system="你是学术检索查询优化器，只输出 JSON。",
        temperature=0.1,
        max_tokens=300,
    )

    match = re.search(r"\{.*\}", response, re.DOTALL)
    if match:
        data = json.loads(match.group())
        en_queries = data.get("en_queries", [query])
        if not isinstance(en_queries, list) or not en_queries:
            en_queries = [query]
        return {"en_queries": en_queries}
    return {"en_queries": [query]}


def _extract_keywords_from_results(results: list[dict]) -> str:
    texts = [result.get("content", "") for result in results[:5]]
    words = []
    for text in texts:
        words.extend(re.findall(r"[A-Za-z0-9\-]{3,}", text))
    if not words:
        return ""
    return " ".join(sorted(set(words))[:6])


def _merge_results(existing: list[dict], new_items: list[dict]) -> list[dict]:
    seen = set()
    merged = []
    for item in existing + new_items:
        key = item.get("chunk_id") or item.get("note_id") or item.get("id") or item.get("content", "")[:120]
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    merged.sort(key=lambda item: item.get("score", 0), reverse=True)
    return merged


async def _search_papers(queries: list[str], user_id: str, top_k: int = 8) -> list[dict]:
    from backend.skills.base import SkillContext
    from backend.skills.registry import skill_registry

    skill = skill_registry.get("retrieval")
    if not skill:
        return []

    results: list[dict] = []
    seen_chunks: set[str] = set()
    for query in queries[:3]:
        context = SkillContext(user_id=user_id, query=query, metadata={"top_k": top_k, "doc_type": "all"})
        result = await skill.execute(context)
        for item in (result.data or {}).get("results", []):
            chunk_id = item.get("chunk_id", "")
            if chunk_id and chunk_id not in seen_chunks:
                seen_chunks.add(chunk_id)
                results.append(item)

    results.sort(key=lambda item: item.get("score", 0), reverse=True)
    return results[:top_k]


async def _search_notes(query: str, user_id: str, top_k: int = 3) -> list[dict]:
    from backend.services.notes_service import get_notes_service
    from concurrent.futures import ThreadPoolExecutor

    def _run() -> list[dict]:
        return get_notes_service().search_notes(user_id=user_id, query=query, top_k=top_k)

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _run)
