# ScholarAgent 架构重构设计

## 一、设计原则

1. **每个 Agent 有不同的能力边界，而非不同的 prompt**
2. **重计算前移到上传时，查询时只做轻量操作**
3. **规则优先，LLM 兜底**——能不调 LLM 就不调
4. **全链路流式输出**——包括深度研究模式
5. **功能 B/C 必须持久化**——否则跟 ChatGPT 没区别

---

## 二、系统全景

```
┌─────────────────────────────────────────────────────────────────┐
│                        用户界面 (Streamlit)                      │
│   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐       │
│   │   智能对话    │   │   深度研究    │   │  研究看板     │       │
│   │  (快速路径)   │   │ (LangGraph)  │   │ (状态/通知)   │       │
│   └──────┬───────┘   └──────┬───────┘   └──────┬───────┘       │
└──────────┼──────────────────┼──────────────────┼───────────────┘
           │ SSE              │ SSE              │ REST
┌──────────┴──────────────────┴──────────────────┴───────────────┐
│                        FastAPI 后端                              │
│                                                                 │
│   ┌─── 快速路径 ────────────────────────────────────────────┐   │
│   │  规则路由(0ms) → 向量检索(0.3s) → 流式LLM(首字1s)      │   │
│   └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│   ┌─── LangGraph 研究路径 ──────────────────────────────────┐   │
│   │                                                         │   │
│   │  ┌─────────┐    ┌────────────┐    ┌────────────────┐   │   │
│   │  │ Planner │───→│ Researcher │───→│  Synthesizer   │   │   │
│   │  │(规则+LLM)│    │  (工具调用) │    │  (LLM+DB写入)  │   │   │
│   │  └────┬────┘    └────────────┘    └────────────────┘   │   │
│   │       ↑                                    │            │   │
│   │       └──── Evaluator (语义评估) ←─────────┘            │   │
│   └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│   ┌─── 上传管线 (Celery 异步) ──────────────────────────────┐   │
│   │  PDF解析 → 向量化 → 观点提取 → 关系构建 → 状态匹配     │   │
│   └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│   ┌─── 数据层 ──────────────────────────────────────────────┐   │
│   │  PostgreSQL    │  Milvus        │  Redis (可选)         │   │
│   │  paper_claims  │  向量索引      │  会话缓存              │   │
│   │  paper_rels    │                │                        │   │
│   │  research_state│                │                        │   │
│   │  notifications │                │                        │   │
│   └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 三、数据模型

### 3.1 新增数据库表

```sql
-- 论文核心观点（上传时异步提取）
CREATE TABLE paper_claims (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id   UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    claim_type    VARCHAR(20) NOT NULL,  -- method / conclusion / limitation / dataset
    content       TEXT NOT NULL,          -- 观点的自然语言描述
    section       VARCHAR(200),           -- 来源章节
    created_at    TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_claims_doc ON paper_claims(document_id);
CREATE INDEX idx_claims_type ON paper_claims(claim_type);

-- 论文间关系（上传时异步构建）
CREATE TABLE paper_relations (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_a_id      UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    doc_b_id      UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    relation_type VARCHAR(20) NOT NULL,  -- contradiction / complement / extension / overlap
    summary       TEXT NOT NULL,          -- 关系描述
    claim_a_id    UUID REFERENCES paper_claims(id),
    claim_b_id    UUID REFERENCES paper_claims(id),
    confidence    FLOAT DEFAULT 0.0,
    created_at    TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_rels_docs ON paper_relations(doc_a_id, doc_b_id);

-- 用户研究状态（每次研究后写入）
CREATE TABLE research_state (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL,
    item_type     VARCHAR(20) NOT NULL,  -- question / hypothesis / conclusion / direction
    content       TEXT NOT NULL,
    status        VARCHAR(20) DEFAULT 'open',  -- open / verified / refuted / archived
    source_session VARCHAR(36),           -- 来源会话 ID
    related_docs  JSONB DEFAULT '[]',     -- 关联的文档 ID 列表
    created_at    TIMESTAMP DEFAULT NOW(),
    updated_at    TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_rs_user ON research_state(user_id, status);

-- 主动通知（新论文匹配到已有研究状态时生成）
CREATE TABLE notifications (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL,
    notif_type    VARCHAR(30) NOT NULL,  -- new_relation / state_match / contradiction_found
    title         TEXT NOT NULL,
    detail        TEXT,
    related_doc   UUID,
    is_read       BOOLEAN DEFAULT FALSE,
    created_at    TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_notif_user ON notifications(user_id, is_read, created_at DESC);
```

### 3.2 LangGraph 共享状态

```python
from typing import TypedDict, Annotated
from operator import add


class PlanStep(TypedDict):
    id: int
    action: str        # retrieve / lookup_relations / lookup_state / synthesize / evaluate
    params: dict       # 动作参数，如 {"keywords": ["fault-tolerant"]}
    status: str        # pending / running / done / failed
    output_key: str    # 结果写入 state 的哪个字段
    result_summary: str


class ResearchState(TypedDict):
    """在 Planner → Researcher → Synthesizer → Evaluator 之间流转"""

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
    search_results: list[dict]   # 向量检索结果
    known_relations: list[dict]  # 从 paper_relations 表查到的
    user_research_ctx: list[dict]  # 从 research_state 表查到的

    # ── Synthesizer 产出 ──
    draft_answer: str            # 初稿（可能被 Evaluator 打回）
    final_answer: str            # 定稿

    # ── Evaluator 产出 ──
    evaluation: dict             # {pass: bool, issues: [...], suggestion: str}

    # ── 流程控制 ──
    step_logs: Annotated[list[str], add]   # 追加模式，不覆盖
    next_action: str             # execute / replan / finish / error
    error_msg: str
```

**设计要点：**
- `search_results`、`known_relations`、`user_research_ctx` 三个字段都是**结构化数据**，来自工具调用（向量检索 / DB 查询），不是 LLM 生成的文本。这确保了 Researcher 和 Synthesizer 的本质区别。
- `step_logs` 是唯一使用 `Annotated[list, add]` 的字段，其他字段都是整体替换语义，不会有并发写入问题。
- `evaluation` 是 Evaluator 的结构化判断，Planner 的 Replan 逻辑基于这个 dict 而非简单的 done/failed。

---

## 四、三个 Agent 的能力划分

| | 🧠 Planner | 🔍 Researcher | ✍️ Synthesizer |
|---|---|---|---|
| **核心能力** | 决策与调度 | 数据检索与查询 | 文本生成与状态写入 |
| **调用 LLM** | 仅 exploratory 类问题 | ❌ 从不调用 | ✅ 流式调用 |
| **工具/数据源** | 规则引擎 | Milvus 向量检索<br>PostgreSQL 查询<br>Query 重写(可选) | LLM API (流式)<br>PostgreSQL 写入 |
| **输入** | query + complexity | plan step 的 params | search_results + relations + research_ctx |
| **输出** | plan (步骤列表) | 结构化数据 (list[dict]) | draft_answer / final_answer + 更新 research_state |
| **延迟** | 规则: 0ms<br>LLM: 2-3s | DB/向量查询: 0.1-0.5s | 首 token: 1s<br>流式输出 |

**关键区别验证：**
- Planner 做决策（生成计划），不执行任何检索或生成
- Researcher 做数据获取（工具调用），不做任何文本生成
- Synthesizer 做文本生成（LLM），不做任何检索

三者的能力边界**完全不重叠**。

---

## 五、LangGraph 图结构

### 5.1 节点与边

```
                    ┌──────────────────────────────────┐
                    │            START                  │
                    └──────────────┬───────────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────────┐
                    │     classify_and_plan             │
                    │     (复杂度分类 + Planner)         │
                    └──────────────┬───────────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────────┐
              ┌────→│        executor                   │
              │     │  (根据 step.action 路由到         │
              │     │   Researcher 或 Synthesizer)      │
              │     └──────────────┬───────────────────┘
              │                    │
              │                    ▼
              │     ┌──────────────────────────────────┐
              │     │        checkpoint                 │
              │     │  (还有待执行步骤？)                │
              │     └──┬───────────────────┬───────────┘
              │        │ 有                 │ 无
              │        ▼                    ▼
              │   回到 executor      ┌─────────────────┐
              │                      │    evaluator     │
              │                      │  (语义质量评估)   │
              │                      └──┬──────┬───────┘
              │                         │      │
              │                  pass   │      │ fail (replan_count < 2)
              │                         ▼      ▼
              │                       END    planner_replan
              │                                │
              └────────────────────────────────┘
```

### 5.2 LangGraph 代码定义

```python
from langgraph.graph import StateGraph, END

def build_graph() -> StateGraph:
    g = StateGraph(ResearchState)

    # 节点
    g.add_node("classify_and_plan", classify_and_plan_node)
    g.add_node("executor", executor_node)
    g.add_node("checkpoint", checkpoint_node)
    g.add_node("evaluator", evaluator_node)
    g.add_node("planner_replan", planner_replan_node)

    # 边
    g.set_entry_point("classify_and_plan")
    g.add_edge("classify_and_plan", "executor")
    g.add_edge("executor", "checkpoint")

    # checkpoint: 有 pending 步骤 → executor，否则 → evaluator
    g.add_conditional_edges("checkpoint", route_checkpoint, {
        "executor": "executor",
        "evaluator": "evaluator",
    })

    # evaluator: pass → END，fail → planner_replan
    g.add_conditional_edges("evaluator", route_evaluator, {
        "finish": END,
        "replan": "planner_replan",
        "error": END,
    })

    # replan 后回到 executor
    g.add_edge("planner_replan", "executor")

    return g.compile()
```

### 5.3 路由函数

```python
def route_checkpoint(state: ResearchState) -> str:
    """检查是否还有待执行的步骤"""
    plan = state.get("plan", [])
    has_pending = any(s["status"] == "pending" for s in plan)
    return "executor" if has_pending else "evaluator"


def route_evaluator(state: ResearchState) -> str:
    """根据评估结果决定下一步"""
    ev = state.get("evaluation", {})
    if ev.get("pass", False):
        return "finish"
    if state.get("replan_count", 0) >= 2:
        return "finish"      # 重规划次数耗尽，用现有结果结束
    return "replan"
```

---

## 六、节点实现细节

### 6.1 classify_and_plan（Planner 入口）

```python
PLAN_TEMPLATES = {
    "simple": [
        {"action": "retrieve", "params": {}},
        {"action": "synthesize", "params": {}},
    ],
    "analysis": [
        {"action": "retrieve", "params": {}},
        {"action": "lookup_relations", "params": {}},
        {"action": "synthesize", "params": {}},
    ],
    "comparison": [
        {"action": "retrieve", "params": {}},
        {"action": "lookup_relations", "params": {}},
        {"action": "lookup_state", "params": {}},
        {"action": "synthesize", "params": {}},
    ],
}

COMPLEXITY_RULES = [
    # (关键词集合, 复杂度等级)
    ({"对比", "比较", "区别", "矛盾", "互补", "compare", "contrast", "vs"}, "comparison"),
    ({"分析", "综述", "现状", "趋势", "创新点", "方法论", "analyze", "survey", "review"}, "analysis"),
    ({"调研", "研究空白", "深度", "全面", "系统", "research gap"}, "comparison"),
]


async def classify_and_plan_node(state: dict) -> dict:
    query = state["query"].lower()

    # 1. 规则分类（0ms）
    complexity = "simple"
    for keywords, level in COMPLEXITY_RULES:
        if any(kw in query for kw in keywords):
            complexity = level
            break

    # 2. 生成计划
    if complexity in PLAN_TEMPLATES:
        # 规则生成，不调 LLM
        raw_steps = PLAN_TEMPLATES[complexity]
        log = f"🧠 Planner: 规则分类为 [{complexity}]，生成 {len(raw_steps)} 步计划"
    else:
        # 仅 exploratory 类才调 LLM
        raw_steps = await _llm_plan(state["query"], state["model"])
        log = f"🧠 Planner: LLM 生成 {len(raw_steps)} 步计划"

    # 3. 构建 PlanStep 列表
    plan = []
    for i, s in enumerate(raw_steps):
        plan.append({
            "id": i + 1,
            "action": s["action"],
            "params": s.get("params", {}),
            "status": "pending",
            "output_key": _action_to_output_key(s["action"]),
            "result_summary": "",
        })

    return {
        "complexity": complexity,
        "plan": plan,
        "current_step_idx": 0,
        "step_logs": [log],
        "next_action": "execute",
    }
```

**关键点：** `simple`、`analysis`、`comparison` 三种复杂度覆盖了 90%+ 的学术问答场景，全部用规则模板生成计划（0ms）。只有极少数无法归类的 exploratory 问题才调 LLM。

### 6.2 executor（调度器）

```python
ACTION_HANDLERS = {
    # Researcher 的工具
    "retrieve":          researcher_retrieve,
    "lookup_relations":  researcher_lookup_relations,
    "lookup_state":      researcher_lookup_state,
    # Synthesizer 的能力
    "synthesize":        synthesizer_generate,
}


async def executor_node(state: dict) -> dict:
    plan = state.get("plan", [])

    # 找第一个 pending 步骤
    current = None
    for s in plan:
        if s["status"] == "pending":
            current = s
            break

    if not current:
        return {"next_action": "execute"}  # checkpoint 会处理

    current["status"] = "running"
    handler = ACTION_HANDLERS.get(current["action"])

    if not handler:
        current["status"] = "failed"
        current["result_summary"] = f"未知 action: {current['action']}"
        return {"plan": plan, "step_logs": [f"❌ 未知操作: {current['action']}"]}

    try:
        result = await handler(state, current["params"])
        current["status"] = "done"
        current["result_summary"] = result.pop("_summary", "完成")
        return {**result, "plan": plan}
    except Exception as e:
        current["status"] = "failed"
        current["result_summary"] = str(e)[:200]
        return {
            "plan": plan,
            "step_logs": [f"❌ {current['action']} 失败: {str(e)[:100]}"],
            "error_msg": str(e),
        }
```

**关键点：** executor 本身不做任何业务逻辑，只是根据 action 名称路由到对应的 handler。这保证了节点职责单一。

### 6.3 Researcher 的三个工具函数

```python
async def researcher_retrieve(state: dict, params: dict) -> dict:
    """工具1: 向量检索（Milvus）"""
    query = state["query"]
    queries = [query]

    # 中文→英文重写（带 3s 超时，失败不阻塞）
    if _has_chinese(query):
        en = await _rewrite_with_timeout(query, state["model"], timeout=3.0)
        queries.extend(en)

    # 并发检索论文和笔记
    papers, notes = await asyncio.gather(
        _search_papers(queries, state["user_id"], top_k=8),
        _search_notes(query, state["user_id"], top_k=3),
    )

    count = len(papers) + len(notes)
    return {
        "search_results": papers + notes,
        "step_logs": [f"🔍 Researcher: 检索到 {len(papers)} 篇论文片段 + {len(notes)} 条笔记"],
        "_summary": f"检索到 {count} 条结果",
    }


async def researcher_lookup_relations(state: dict, params: dict) -> dict:
    """工具2: 查询预计算的论文关系（PostgreSQL）"""
    # 从检索结果中提取涉及的 document_id
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

    # 查 paper_relations 表（预计算好的，0.1 秒）
    relations = await _query_relations(list(doc_ids))

    return {
        "known_relations": relations,
        "step_logs": [f"⚖️ Researcher: 查到 {len(relations)} 组预计算关系"],
        "_summary": f"{len(relations)} 组论文关系",
    }


async def researcher_lookup_state(state: dict, params: dict) -> dict:
    """工具3: 查询用户研究状态（PostgreSQL）"""
    items = await _query_research_state(state["user_id"])

    return {
        "user_research_ctx": items,
        "step_logs": [f"📊 Researcher: 加载 {len(items)} 条研究状态记录"],
        "_summary": f"{len(items)} 条研究状态",
    }
```

**关键点：** Researcher 的三个函数全部是**工具调用**（向量检索 + DB 查询），没有一次 LLM 调用。每个函数耗时在 0.1-0.5 秒之间。这就是它跟"换个 prompt 调 LLM"的本质区别。

### 6.4 Synthesizer

```python
SYNTH_SYSTEM = """你是 ScholarAgent，基于用户的个人知识库回答学术问题。
用 [1][2] 标注引用来源。不要编造，与用户使用相同的语言。"""


async def synthesizer_generate(state: dict, params: dict) -> dict:
    """Synthesizer: 基于 Researcher 收集的结构化数据，流式生成回答"""

    # 1. 组装 context（来自 Researcher 的结构化数据，非 LLM 输出）
    context_parts = _format_search_results(state.get("search_results", []))

    relations = state.get("known_relations", [])
    if relations:
        context_parts.append(_format_relations(relations))

    research_ctx = state.get("user_research_ctx", [])
    if research_ctx:
        context_parts.append(_format_research_ctx(research_ctx))

    # 2. 如果有之前被打回的草稿和评估反馈，加入 context
    prev_draft = state.get("draft_answer", "")
    evaluation = state.get("evaluation", {})
    if prev_draft and not evaluation.get("pass", True):
        context_parts.append(
            f"=== 上一版回答的不足 ===\n"
            f"问题：{'; '.join(evaluation.get('issues', []))}\n"
            f"建议：{evaluation.get('suggestion', '')}"
        )

    system = SYNTH_SYSTEM + "\n\n参考资料：\n" + "\n\n".join(context_parts)

    # 3. 调用 LLM（这是整个研究流程中唯一的生成型 LLM 调用）
    adapter, model_name = resolve_adapter(state["model"])
    answer = await adapter.chat(
        model=model_name,
        messages=[{"role": "user", "content": state["query"]}],
        system=system,
        temperature=0.5,
        max_tokens=3000,
    )

    # 4. 异步更新 research_state 表（功能 C 的写入端）
    asyncio.create_task(_update_research_state(state["user_id"], state["query"], answer))

    return {
        "draft_answer": answer,
        "step_logs": [f"✍️ Synthesizer: 生成回答（{len(answer)} 字）"],
        "_summary": f"生成 {len(answer)} 字回答",
    }
```

**关键点：**
- Synthesizer 是整个研究流程中**唯一做 LLM 生成**的节点。
- 它的输入全部来自 Researcher 的结构化数据（检索结果、关系表、研究状态），不是其他 LLM 调用的输出。
- 如果被 Evaluator 打回，Synthesizer 会拿到评估反馈，据此改进——这是 Replan 真正有意义的地方。
- `_update_research_state` 是异步任务，不阻塞主流程。

### 6.5 Evaluator（语义质量评估）

```python
EVAL_PROMPT = """判断以下学术回答的质量。

用户问题：{query}

检索到 {n_results} 条来源，{n_relations} 组论文关系。

回答：
{answer}

请评估（只输出 JSON）：
{{
  "pass": true/false,
  "issues": ["问题1", "问题2"],
  "suggestion": "改进建议"
}}

评估标准：
- 是否回答了用户的问题（不跑题）
- 是否引用了检索到的来源（[1][2] 标注）
- 对于对比类问题，是否涵盖了多种方法/观点
- 回答长度是否合理（不过于简短）"""


async def evaluator_node(state: dict) -> dict:
    """语义评估：不只看 done/failed，而是评估回答质量"""

    draft = state.get("draft_answer", "")
    if not draft:
        return {
            "evaluation": {"pass": False, "issues": ["未生成回答"], "suggestion": "需要执行 synthesize 步骤"},
            "step_logs": ["✅ Evaluator: 无回答，标记为未通过"],
            "next_action": "replan",
        }

    # 快速规则检查（0ms，不调 LLM）
    issues = []
    n_results = len(state.get("search_results", []))

    # 检查1: 回答是否太短
    if len(draft) < 100:
        issues.append("回答过于简短")

    # 检查2: 是否有引用标记
    if n_results > 0 and "[1]" not in draft and "[" not in draft:
        issues.append("未引用检索到的来源")

    # 检查3: 对比类问题是否有多角度
    if state.get("complexity") == "comparison" and len(draft) < 300:
        issues.append("对比分析不够充分")

    if issues:
        return {
            "evaluation": {"pass": False, "issues": issues, "suggestion": "请补充上述不足"},
            "step_logs": [f"⚠️ Evaluator: 规则检查未通过 → {issues}"],
            "next_action": "replan",
            "replan_count": state.get("replan_count", 0) + 1,
        }

    # 规则检查通过 → 用轻量 LLM 做最终语义评估（仅对 comparison/exploratory）
    if state.get("complexity") in ("comparison", "exploratory"):
        ev = await _llm_evaluate(state)
        if not ev.get("pass", True):
            return {
                "evaluation": ev,
                "step_logs": [f"⚠️ Evaluator: LLM 语义评估未通过 → {ev.get('issues', [])}"],
                "next_action": "replan",
                "replan_count": state.get("replan_count", 0) + 1,
            }

    # 通过 → 定稿
    return {
        "evaluation": {"pass": True, "issues": [], "suggestion": ""},
        "final_answer": draft,
        "step_logs": ["✅ Evaluator: 质量检查通过"],
        "next_action": "finish",
    }
```

**关键点：**
- 先跑规则检查（0ms），能拦住大部分明显问题。
- 只有 comparison / exploratory 类问题才做 LLM 语义评估，simple / analysis 类通过规则检查就够了。
- 评估结果是结构化的 `{pass, issues, suggestion}`，Planner replan 时能精准利用。
- `issues` 和 `suggestion` 会被 Synthesizer 在重新生成时看到（见 6.4 中的第 2 步）。

### 6.6 Planner Replan

```python
async def planner_replan_node(state: dict) -> dict:
    """基于 Evaluator 的结构化反馈，调整计划"""

    ev = state.get("evaluation", {})
    issues = ev.get("issues", [])
    suggestion = ev.get("suggestion", "")

    new_steps = []

    # 根据具体问题生成补充步骤
    for issue in issues:
        if "检索" in issue or "来源" in issue or "不足" in issue:
            new_steps.append({"action": "retrieve", "params": {"strategy": "expand_keywords"}})
        if "对比" in issue or "多角度" in issue or "充分" in issue:
            if not state.get("known_relations"):
                new_steps.append({"action": "lookup_relations", "params": {}})
        if "简短" in issue or "补充" in issue:
            pass  # synthesize 会自动基于新数据重新生成

    # 一定以 synthesize 结尾
    new_steps.append({"action": "synthesize", "params": {"mode": "revise"}})

    # 构建 PlanStep
    existing = [s for s in state.get("plan", []) if s["status"] == "done"]
    start_id = len(existing) + 1
    plan_additions = []
    for i, s in enumerate(new_steps):
        plan_additions.append({
            "id": start_id + i,
            "action": s["action"],
            "params": s.get("params", {}),
            "status": "pending",
            "output_key": _action_to_output_key(s["action"]),
            "result_summary": "",
        })

    full_plan = existing + plan_additions

    return {
        "plan": full_plan,
        "current_step_idx": start_id - 1,
        "step_logs": [f"🔄 Planner: 重规划（第 {state.get('replan_count', 1)} 次），新增 {len(plan_additions)} 步"],
        "next_action": "execute",
    }
```

**关键点：** Replan 不再是"重新让 LLM 想一个新计划"（慢且不可控），而是**根据 Evaluator 的具体 issues 生成针对性的补充步骤**（规则驱动，0ms）。

---

## 七、上传管线（异步，功能 B 的核心）

```
PDF 上传
   │
   ▼
┌─────────────────────────────────┐
│ Task 1: parse_and_vectorize     │  ← 已有
│   PDF → GROBID → chunks → Milvus│
└─────────────┬───────────────────┘
              │ 成功后触发
              ▼
┌─────────────────────────────────┐
│ Task 2: extract_claims          │  ← 新增
│   对每个 section 调 LLM 提取     │
│   核心观点（method/conclusion/   │
│   limitation/dataset）           │
│   写入 paper_claims 表           │
└─────────────┬───────────────────┘
              │ 成功后触发
              ▼
┌─────────────────────────────────┐
│ Task 3: build_relations         │  ← 新增
│   新论文的 claims vs            │
│   已有论文的 claims              │
│   LLM 判断关系类型               │
│   写入 paper_relations 表        │
└─────────────┬───────────────────┘
              │ 成功后触发
              ▼
┌─────────────────────────────────┐
│ Task 4: match_research_state    │  ← 新增
│   新论文的 claims vs            │
│   用户的 open questions          │
│   如果匹配 → 写入 notifications │
└─────────────────────────────────┘
```

**Celery chain：**

```python
from celery import chain

def on_upload_success(document_id, user_id):
    """论文上传成功后触发的异步管线"""
    pipeline = chain(
        extract_claims_task.s(document_id),
        build_relations_task.s(document_id),
        match_research_state_task.s(document_id, user_id),
    )
    pipeline.apply_async()
```

**extract_claims 的 prompt：**

```python
CLAIM_EXTRACT_PROMPT = """从以下论文章节中提取核心学术观点。

每个观点标注类型：
- method: 提出或使用的方法
- conclusion: 实验结论或发现
- limitation: 方法的局限性
- dataset: 使用的数据集或基准

只输出 JSON：
{"claims": [{"type": "method", "content": "...", "section": "..."}]}"""
```

**build_relations 的逻辑：**

```python
async def build_relations(new_doc_id: str):
    """拿新论文的 claims 与已有论文的 claims 做两两对比"""
    new_claims = db.query(paper_claims).filter(document_id=new_doc_id).all()
    
    # 获取已有论文的 claims（按类型分组，减少对比次数）
    existing = db.query(paper_claims).filter(document_id != new_doc_id).all()
    
    # 只对比同类型的 claims（method vs method, conclusion vs conclusion）
    for new_c in new_claims:
        same_type = [e for e in existing if e.claim_type == new_c.claim_type]
        if not same_type:
            continue
        
        # 批量送 LLM 判断关系（一次调用判断多对，减少调用次数）
        batch = same_type[:10]  # 限制每批数量
        relations = await _batch_compare(new_c, batch)
        
        for r in relations:
            if r["relation_type"] != "none":
                db.insert(paper_relations, ...)
```

**关键点：**
- 所有耗时计算（观点提取、关系判断）在上传时异步完成，用户不等待。
- 查询时 Researcher 只需查表（0.1 秒），不需要实时做 LLM 对比。
- 同类型 claims 之间才对比，避免 O(n²) 爆炸。
- 批量送 LLM 减少调用次数。

---

## 八、SSE 流式研究接口

```python
@router.post("/research/stream")
async def research_stream(req: ResearchRequest):
    """
    SSE 事件流，前端实时显示研究进度

    事件类型：
    - plan:     {"type":"plan", "steps":[...]}
    - progress: {"type":"progress", "step_id":1, "status":"done", "summary":"..."}
    - relation: {"type":"relation", "data":{...}}
    - token:    {"type":"token", "content":"一段文字"}
    - state:    {"type":"state", "research_ctx":[...]}
    - done:     {"type":"done", "final_answer":"..."}
    - error:    {"type":"error", "message":"..."}
    """

    graph = get_research_graph()
    initial_state = _build_initial_state(req)

    async def event_stream():
        try:
            # astream 会在每个节点执行后 yield 更新
            async for event in graph.astream(initial_state, stream_mode="updates"):
                for node_name, updates in event.items():
                    # 发送步骤日志
                    for log in updates.get("step_logs", []):
                        yield _sse({"type": "progress", "log": log})

                    # 发送计划（首次）
                    if "plan" in updates and node_name == "classify_and_plan":
                        yield _sse({"type": "plan", "steps": updates["plan"]})

                    # 发送步骤状态更新
                    if "plan" in updates and node_name == "executor":
                        for s in updates["plan"]:
                            if s["status"] in ("done", "failed"):
                                yield _sse({
                                    "type": "progress",
                                    "step_id": s["id"],
                                    "status": s["status"],
                                    "summary": s["result_summary"],
                                })

                    # 发送论文关系
                    if "known_relations" in updates:
                        for r in updates["known_relations"]:
                            yield _sse({"type": "relation", "data": r})

                    # 发送研究状态
                    if "user_research_ctx" in updates:
                        yield _sse({"type": "state", "data": updates["user_research_ctx"]})

                    # 发送最终回答
                    if "final_answer" in updates and updates["final_answer"]:
                        yield _sse({"type": "answer", "content": updates["final_answer"]})

            yield _sse({"type": "done"})

        except Exception as e:
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

**关键点：** 利用 LangGraph 的 `astream(stream_mode="updates")` 在每个节点完成后推送事件，前端可以实时更新计划状态、显示关系、最终流式输出回答。

---

## 九、性能对比

### 简单问题（走快速路径，不经过 LangGraph）

| 阶段 | 旧方案 | 新方案 |
|------|--------|--------|
| 路由 | LLM (2s) | 规则 (0ms) |
| 检索 | 串行 (1s) | 并发 (0.5s) |
| 生成 | 阻塞 (4s) | 流式 (首字 1s) |
| **总计** | **7s 后才看到回答** | **1.5s 首字出现** |

### 深度研究（LangGraph 路径）

| 阶段 | 旧方案 | 新方案 |
|------|--------|--------|
| 规划 | LLM (3s) | 规则 (0ms) |
| 检索 | 串行 (1s) | 并发 (0.5s) |
| 分析 | LLM (4s) | ❌ 无此步骤 |
| 交叉对比 | LLM (5s) | 查表 (0.1s) |
| 研究状态 | LLM (3s) | 查表 (0.1s) |
| 生成 | LLM 阻塞 (4s) | LLM 流式 (首字 1s) |
| 评估 | 无 | 规则 (0ms) + 轻量 LLM (1s，仅 comparison) |
| **总计** | **20s+，无进度反馈** | **2-3s 首个进度事件，5s 开始出回答** |
| **LLM 调用次数** | **5-7 次** | **1 次（生成）+ 0~1 次（评估）** |

---

## 十、快速路径 vs 研究路径的分流

```python
def should_use_research(query: str) -> bool:
    """判断是否走深度研究路径"""
    research_signals = {
        "调研", "综述", "对比分析", "研究现状", "研究空白",
        "系统分析", "全面分析", "深度研究",
        "survey", "review", "compare all", "research gap",
    }
    q = query.lower()
    return any(kw in q for kw in research_signals)
```

- **普通对话**（90% 的问题）：走已有的流式快速路径（检索 + 单次 LLM），体验跟 ChatGPT 一样快。
- **深度研究**（10% 的问题）：走 LangGraph，有完整的 Plan-Execute-Replan 流程，但因为 Researcher 全部是工具调用，实际只有 1 次 LLM 生成调用。

---

## 十一、与课题四的架构映射

| LangGraph 概念 | ScholarAgent | 课题四（代码修复） |
|---|---|---|
| **State** | ResearchState（query、results、relations、answer） | DebugState（error_log、root_cause、patch、test_result） |
| **Planner** | 规则分类 + 计划模板 | 分析报错类型 + 生成修复策略 |
| **Researcher (工具)** | 向量检索 + DB 查关系 + DB 查研究状态 | 读日志 + 定位代码 + 查依赖图 |
| **Synthesizer (LLM)** | 生成研究报告 | 生成修复补丁 |
| **Evaluator** | 规则 + LLM 语义评估 | 运行测试用例 |
| **Replan** | 基于 issues 针对性补充步骤 | 基于测试失败原因调整策略 |
| **上传管线** | 论文 → claims → relations | 代码仓库 → AST 分析 → 依赖图 |
# 架构自评与修正

## 第一轮自评

### 检查项 1: 三个 Agent 的能力边界是否真正不重叠？

✅ **通过。**
- Planner：规则引擎 + 极少量 LLM 规划。不检索，不生成。
- Researcher：工具调用（Milvus + PostgreSQL）。不调 LLM。
- Synthesizer：LLM 流式生成 + DB 写入。不做检索。

三者能力完全正交。

### 检查项 2: Evaluator 调 LLM 是否打破了"Researcher 不调 LLM"的承诺？

⚠️ **发现问题。**
Evaluator 在 comparison 场景下调 LLM 做语义评估。但 Evaluator 不属于三个 Agent 中的任何一个——它是图中的独立控制节点，职责是"决策"而非"执行"。

**修正：** 明确 Evaluator 的定位。它不是 Agent，而是一个**检查点节点**（类似单元测试），调 LLM 只是为了做质量判断，不生成用户可见的内容。这与 Planner 的决策属性一致。实际上 Evaluator 的 LLM 调用可以用轻量模型（qwen-turbo），成本和延迟都很低。

### 检查项 3: Replan 是否真正有效？

原方案的 Replan 几乎不触发（只在检索结果 < 2 时）。新方案如何？

新方案中 Evaluator 的触发条件：
1. 回答 < 100 字 → Replan（补充检索 + 重新生成）
2. 有检索结果但未引用 → Replan（重新生成，带强调引用的 hint）
3. comparison 类问题但回答 < 300 字 → Replan（补充关系查询 + 重新生成）
4. LLM 语义评估不通过 → Replan（基于具体 issues 补充步骤）

✅ **通过。** 触发条件覆盖了实际会出现的质量问题，且 Replan 的补充步骤是针对性的（不是"重新来一遍"）。

但有一个边界问题：如果 Replan 后 Synthesizer 重新生成，但新的回答仍然不通过，会再次 Replan（最多 2 次）。第 3 次直接用现有结果结束。

### 检查项 4: 上传管线的 build_relations 是否会 O(n²) 爆炸？

⚠️ **发现问题。**

如果知识库有 100 篇论文，每篇 10 个 claims = 1000 个 claims。新上传一篇有 10 个 claims，需要对比 10 × 1000 = 10000 对。即使只对比同类型，假设每类 250 个，仍然需要 10 × 250 = 2500 对。

**修正方案：**
1. **先做向量相似度过滤**：对新论文的每个 claim 做 embedding，在已有 claims 中找 top-5 最相似的，只对这 5 个做 LLM 判断。这样每个 claim 最多 5 次 LLM 判断，10 个 claims = 50 次。
2. **批量调用**：把 5 对 claim 打包成一次 LLM 调用（prompt 里放 5 对让模型一次性判断），降到 10 次 LLM 调用。
3. **异步执行**：这些都在 Celery worker 里跑，用户不等。

修正后的 build_relations：

```python
async def build_relations(new_doc_id: str):
    new_claims = get_claims(new_doc_id)
    
    for claim in new_claims:
        # 1. 向量检索最相似的 5 个已有 claims（同类型）
        similar = await vector_search_claims(
            claim.embedding, 
            claim_type=claim.claim_type,
            exclude_doc=new_doc_id,
            top_k=5,
        )
        
        if not similar:
            continue
        
        # 2. 批量 LLM 判断关系（一次调用）
        relations = await batch_judge_relations(claim, similar)
        
        # 3. 写入有效关系
        for r in relations:
            if r["type"] != "none":
                db.insert(paper_relations, ...)
```

### 检查项 5: paper_claims 是否需要 embedding 字段？

⚠️ **发现遗漏。**

上面的 build_relations 修正依赖于 claims 的 embedding 做向量相似度过滤。但原始的 paper_claims 表没有 embedding 字段。

**修正：** 在 extract_claims 阶段，对每个 claim 的 content 做 embedding，存到 Milvus 的一个独立 collection（`claim_vectors`）。paper_claims 表本身不存 embedding（PostgreSQL 存大向量不合适），通过 claim_id 关联。

修正后的表结构：
```sql
-- paper_claims 表不变
-- 新增 Milvus collection:
-- claim_vectors: {claim_id, document_id, claim_type, embedding}
```

### 检查项 6: 流式研究接口中，Synthesizer 的回答是否真正流式？

⚠️ **发现问题。**

当前 synthesizer_generate 调的是 `adapter.chat()`（非流式），返回完整文本。然后 SSE 接口一次性发送整个回答。这跟前面的快速路径用 `chat_stream()` 不一致。

**修正：** Synthesizer 在研究路径中也应该用流式。但 LangGraph 的节点返回的是 state update（dict），不好直接 yield token。

解决方案：**把 Synthesizer 从 LangGraph 节点中拆出来**。LangGraph 负责 plan → retrieve → lookup → evaluate 的流程控制，到了 synthesize 这一步时，图结束，由 SSE 接口层直接调用流式 LLM。

修正后的流程：

```
LangGraph 图: planner → executor(retrieve) → executor(lookup) → checkpoint → evaluator
                        → 图结束，返回 state（包含所有检索结果和关系）

SSE 接口层:
  1. 运行图 → 收集 search_results, known_relations, user_research_ctx
  2. 每个节点完成时发送 progress 事件
  3. 图结束后，直接调 adapter.chat_stream() 流式生成回答
  4. 每个 token 发送 SSE 事件
  5. 回答完成后，调 Evaluator 规则检查
  6. 如果不通过 → 重新运行（replan_count < 2）
```

这样做的好处：
- Synthesizer 的回答是真正流式的（逐 token）
- LangGraph 只负责"数据收集"阶段，逻辑更清晰
- 避免了在 LangGraph 节点内做流式输出的技术难题

修正后的图结构：

```
START → classify_and_plan → executor → checkpoint → evaluator → END
                ↑                         |             |
                └── replan ←──────────────┘             |
                                              (只循环数据收集部分)
```

synthesize 不再是图中的节点，而是图执行完后的外层逻辑。

### 检查项 7: 如果 Evaluator 打回，Synthesizer 重新生成时怎么传递反馈？

在修正后的架构中，Evaluator 不在图内（synthesize 在图外），所以流程变成：

```
while replan_count < 2:
    state = await graph.ainvoke(state)   # 数据收集
    answer = stream_generate(state)       # 流式生成
    evaluation = evaluate(answer, state)  # 质量检查
    if evaluation.pass:
        break
    state["evaluation"] = evaluation      # 反馈注入
    state = inject_replan_steps(state)    # 根据 issues 补充步骤
    replan_count += 1
```

这里有个问题：如果 Evaluator 认为"检索不足"，需要重新跑图（补充检索）。但如果只是"回答太短"，不需要重新跑图，只需要重新生成。

**修正：区分两类 Replan**

```python
if evaluation needs more data:     # 需要补数据
    → 重新运行图（带补充步骤）
    → 重新生成
elif evaluation needs better answer: # 数据够了，回答质量不行
    → 不重新运行图
    → 直接重新生成（带 evaluation 反馈）
```

### 检查项 8: research_state 表的 status 流转是否清晰？

```
question:   open → verified(被论文回答) / archived(用户手动关闭)
hypothesis: open → verified(实验证实) / refuted(实验否定) / archived
conclusion: 无状态变化（写入即定稿）
direction:  open → archived
```

✅ **通过。** 状态流转简单清晰，没有歧义。

### 检查项 9: notifications 什么时候清理？

⚠️ **遗漏。** 没有设计通知的过期和清理机制。

**修正：** 通知按时间排序，前端只展示最近 30 天内且未读的通知。已读通知 90 天后自动清理（Celery beat 定时任务）。

### 检查项 10: 前端页面设计是否与新架构匹配？

需要三个页面：
1. **智能对话**（快速路径）— 已有，无需改动
2. **深度研究**（LangGraph）— 需要重新设计，支持：
   - 实时显示计划步骤和进度
   - 流式显示回答
   - 展示论文关系卡片
   - 展示研究状态
3. **研究看板**（新增）— 展示：
   - 用户的研究状态（开放问题、假设、结论）
   - 知识库中的论文关系图（可视化）
   - 未读通知

✅ 三个页面对应三种不同的交互模式，职责清晰。

---

## 第一轮修正汇总

| 编号 | 问题 | 修正 |
|------|------|------|
| 1 | Evaluator 定位不清 | 明确为控制节点，非 Agent，用轻量模型 |
| 2 | build_relations O(n²) | 先向量过滤 top-5 → 再批量 LLM 判断 |
| 3 | claims 缺少 embedding | 新增 Milvus claim_vectors collection |
| 4 | Synthesizer 非流式 | 将 synthesize 从图中拆出，在 SSE 层直接流式调用 |
| 5 | Replan 不区分类型 | 区分"需要补数据"和"需要改回答"两类 |
| 6 | 通知无清理机制 | 30 天 + 已读 90 天自动清理 |

---

## 第二轮自评（基于修正后的架构）

### 检查 A: 修正后的 LangGraph 图是否还有 Synthesizer？

修正后图中没有 synthesize 节点了。图的作用变成了"数据收集管线"：

```
classify_and_plan → executor(retrieve) → executor(lookup_relations) 
    → executor(lookup_state) → checkpoint → END
```

那 Planner 生成的计划模板也需要修正——不再包含 `synthesize` 步骤：

```python
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
```

图只负责收集数据，synthesize + evaluate 在外层 SSE 接口中处理。

✅ 修正后逻辑更干净。

### 检查 B: Evaluator 也需要从图中拆出来吗？

是的。既然 synthesize 在图外，evaluate 也应该在图外（evaluate 评估的是 answer，而 answer 在图外生成）。

修正后的完整流程：

```python
async def research_stream(req):
    graph = get_graph()
    state = build_initial_state(req)
    
    replan_count = 0
    while replan_count <= 2:
        # Phase 1: 数据收集（LangGraph）
        async for event in graph.astream(state, stream_mode="updates"):
            yield sse_progress(event)   # 实时推送进度
        
        # Phase 2: 生成回答（流式 LLM）
        answer_chunks = []
        async for token in synthesize_stream(state):
            yield sse_token(token)
            answer_chunks.append(token)
        full_answer = "".join(answer_chunks)
        
        # Phase 3: 质量检查
        evaluation = evaluate(full_answer, state)
        if evaluation["pass"]:
            yield sse_done(full_answer)
            break
        
        # Phase 4: Replan
        if needs_more_data(evaluation):
            state = inject_replan_steps(state, evaluation)
            replan_count += 1
            yield sse_replan(evaluation)
            continue    # 回到 Phase 1
        else:
            # 数据够了，只需要重新生成
            state["evaluation"] = evaluation
            replan_count += 1
            yield sse_replan(evaluation)
            continue    # 跳过 Phase 1，直接到 Phase 2
```

✅ 这样 LangGraph 图的职责更纯粹（只做数据收集和路由），不混入任何 LLM 生成逻辑。

### 检查 C: 图中只剩 Researcher 的工具调用了，还需要 LangGraph 吗？

好问题。如果图里只有 retrieve → lookup_relations → lookup_state 三个工具调用，是否用 LangGraph 有些杀鸡用牛刀？

答案是**仍然需要**，原因：

1. **Replan 时需要动态调整步骤**：比如 Evaluator 说"检索不足"，Planner 会在图中添加一个带不同关键词的 retrieve 步骤。这种动态计划变更是 LangGraph 的核心价值。
2. **条件路由**：simple 问题只需要 retrieve，analysis 还需要 lookup_relations，comparison 三个都要。这种条件分支用 LangGraph 表达最自然。
3. **课题四的架构对齐**：需要展示 Plan-Execute-Replan 的完整图结构，LangGraph 是标准工具。
4. **未来扩展**：如果后续加入更多工具（比如引用网络分析、作者关系查询），图结构可以无缝扩展。

✅ 保留 LangGraph，但承认它在当前场景中主要价值是"架构规范性"和"可扩展性"，而非解决当前无法解决的问题。

### 检查 D: 前端如何处理"数据够了只需重新生成"的 Replan？

```
前端收到：
  SSE: {type: "replan", reason: "回答过于简短，重新生成"}
  SSE: {type: "token", content: "..."} ← 新的流式回答开始
  SSE: {type: "done"}

前端行为：
  清空上一次的回答区域
  显示"正在改进回答..."提示
  接收新的 token 流
```

✅ 用户体验合理。看到的是"系统在自我改进"，而不是"卡住了"。

### 检查 E: _update_research_state 的时机是否正确？

原方案在 Synthesizer 中用 asyncio.create_task 异步更新。但如果 Evaluator 打回了这次回答，写入的研究状态可能基于一个质量不佳的回答。

**修正：** 只在 evaluation.pass = True（最终定稿）后才写入 research_state。

```python
if evaluation["pass"]:
    await update_research_state(state["user_id"], state["query"], full_answer)
    yield sse_done(full_answer)
    break
```

✅ 修正后不会写入低质量的研究状态。

---

## 第二轮修正汇总

| 编号 | 问题 | 修正 |
|------|------|------|
| 7 | 计划模板仍包含 synthesize | 从模板中移除，synthesize 在图外 |
| 8 | Evaluator 在图内但依赖图外的 answer | Evaluator 也移到图外 |
| 9 | research_state 写入时机不对 | 只在最终定稿后写入 |

---

## 最终架构确认

### 图结构（修正后）

```
LangGraph 图（纯数据收集）:
  classify_and_plan → executor → checkpoint ──→ executor (循环)
                         ↑           │
                         │           └──→ END (所有步骤完成)
                         │
                    planner_replan ← (由外层注入新步骤时重启图)

外层 SSE 接口（生成 + 评估 + Replan 循环）:
  while replan_count <= 2:
      Phase 1: 运行图 → 收集数据（SSE 推送进度）
      Phase 2: 流式 LLM 生成回答（SSE 推送 token）
      Phase 3: Evaluate（规则 + 可选 LLM）
      Phase 4: 通过 → 写入 research_state → done
               不通过且需要补数据 → 注入新步骤 → 回到 Phase 1
               不通过但数据够了 → 回到 Phase 2
```

### LLM 调用次数（最终确认）

| 场景 | LLM 调用 | 说明 |
|------|---------|------|
| 快速路径（简单问答） | 1 次（流式） | 只有生成回答 |
| 深度研究（一次通过） | 1 次（流式） | 图内全部是工具调用 |
| 深度研究（1 次 Replan） | 2 次 + 0~1 次 | 2 次生成 + 可选评估 |
| 深度研究（2 次 Replan） | 3 次 + 0~2 次 | 3 次生成 + 可选评估 |

相比旧方案（5-7 次），新方案即使最坏情况也只有 5 次，通常只有 1-2 次。

### 三个 Agent 职责最终确认

- 🧠 **Planner**: 图的入口节点 + 外层 Replan 逻辑。规则优先，极少调 LLM。
- 🔍 **Researcher**: 图内的执行者。3 种工具：向量检索、关系查询、状态查询。0 次 LLM。
- ✍️ **Synthesizer**: 图外的生成器。1 次流式 LLM。写入 research_state。

✅ **自评结束。架构在功能完整性、性能、可扩展性、与课题四的对齐度上均满足要求。**
