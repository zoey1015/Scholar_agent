"""
Deep research page.
"""

import json
import os

import httpx
import streamlit as st

st.set_page_config(page_title="深度研究 - ScholarAgent", page_icon="🔬", layout="wide")

API_BASE = os.getenv("API_BASE_URL", "http://backend:8000/api/v1")

st.markdown(
    """
<style>
    #MainMenu, footer, header {visibility: hidden;}
    .block-container { padding-top: 1.5rem !important; }
    .step-card { padding: 10px 14px; margin: 5px 0; border-radius: 8px; font-size: 0.88rem; }
    .step-done { background: #f0fdf4; border: 1px solid #86efac; }
    .step-running { background: #eff6ff; border: 1px solid #93c5fd; }
    .step-failed { background: #fef2f2; border: 1px solid #fca5a5; }
    .step-pending { background: #f9fafb; border: 1px solid #e5e7eb; color: #9ca3af; }
    .relation-card { padding: 10px 14px; margin: 5px 0; border-radius: 8px; font-size: 0.85rem; background: #fefce8; border: 1px solid #fde68a; }
    .source-card { padding: 10px 14px; margin: 6px 0; border-radius: 8px; font-size: 0.84rem; background: #f8fafc; border-left: 3px solid #0ea5e9; }
    .source-title { font-weight: 600; color: #0f172a; margin-bottom: 4px; }
    .source-meta { color: #64748b; font-size: 0.78rem; margin-top: 4px; }
    .log-item { font-size: 0.82rem; color: #4b5563; padding: 2px 0; }
    .replan-banner { padding: 8px 14px; margin: 8px 0; border-radius: 8px; background: #fff7ed; border: 1px solid #fed7aa; font-size: 0.85rem; }
</style>
""",
    unsafe_allow_html=True,
)

st.markdown("## 🔬 深度研究")
st.caption("Plan-Execute-Replan · 多步自主推理 · 跨论文关系检测")
st.divider()

if "latest_research_result" not in st.session_state:
    st.session_state.latest_research_result = None
if "latest_research_query" not in st.session_state:
    st.session_state.latest_research_query = ""
if "latest_research_model" not in st.session_state:
    st.session_state.latest_research_model = ""

col1, col2 = st.columns([4, 1])
with col1:
    query = st.text_area(
        "研究问题",
        placeholder="例如：调研知识库里关于容错控制的研究现状，对比各方法的优劣，找出研究空白",
        height=80,
        label_visibility="collapsed",
    )
with col2:
    model_options = ["qwen3-max-2026-01-23", "qwen3.5-plus", "qwen-plus", "deepseek-chat"]
    model = st.selectbox("模型", model_options, label_visibility="collapsed")
    run_clicked = st.button("🚀 开始研究", type="primary", use_container_width=True)


def _render_plan(placeholder, plan: list, complexity: str = ""):
    if not plan:
        return

    action_icons = {"retrieve": "🔍", "lookup_relations": "⚖️", "lookup_state": "📊"}
    status_icons = {"done": "✅", "running": "🔄", "failed": "❌", "pending": "⏳"}

    parts = []
    if complexity:
        parts.append(f"**📋 研究计划** · 复杂度: `{complexity}`\n")

    for step in plan:
        status = step.get("status", "pending")
        summary = step.get("result_summary", "")
        summary_text = f" — {summary}" if summary else ""
        parts.append(
            f'<div class="step-card step-{status}">{status_icons.get(status, "❓")} {action_icons.get(step.get("action", ""), "📌")} <b>Step {step.get("id", "?")}</b>: {step.get("action", "")}{summary_text}</div>'
        )

    placeholder.markdown("\n".join(parts), unsafe_allow_html=True)


def _render_logs(placeholder, logs: list):
    placeholder.markdown("\n".join([f'<div class="log-item">{log}</div>' for log in logs[-15:]]), unsafe_allow_html=True)


def _render_sources(sources: list[dict]):
    if not sources:
        st.info("没有可展示的参考文献（可能检索分数较低或当前问题不需要引用）。")
        return

    for idx, source in enumerate(sources, start=1):
        s_type = source.get("type", "paper")
        icon = "📄" if s_type == "paper" else "📝"
        title = source.get("section_title") or "未命名片段"
        content = source.get("content", "")
        score = source.get("score", 0)
        doc_id = source.get("document_id") or source.get("note_id") or ""
        st.markdown(
            f'<div class="source-card">'
            f'<div class="source-title">{icon} [{idx}] {title}</div>'
            f'<div>{content}</div>'
            f'<div class="source-meta">score={score:.3f} · id={doc_id}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


def run_research_stream(query: str, model: str):
    result = {"plan": [], "logs": [], "relations": [], "research_ctx": [], "sources": [], "answer_parts": [], "replans": []}
    plan_container = st.container()
    log_expander = st.expander("📝 执行日志", expanded=True)
    answer_container = st.container()

    plan_placeholder = plan_container.empty()
    log_placeholder = log_expander.empty()
    answer_placeholder = answer_container.empty()

    try:
        with httpx.Client(timeout=300.0) as client:
            with client.stream("POST", f"{API_BASE}/research/stream", json={"query": query, "model": model}) as response:
                if response.status_code != 200:
                    st.error(f"请求失败: {response.status_code}")
                    return result

                current_answer = ""
                for line in response.iter_lines():
                    if not line:
                        continue
                    if isinstance(line, bytes):
                        line = line.decode("utf-8", errors="ignore")
                    if not line.startswith("data: "):
                        continue

                    payload = line[6:].strip()
                    if not payload:
                        continue

                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    etype = event.get("type")
                    if etype == "plan":
                        result["plan"] = event.get("steps", [])
                        _render_plan(plan_placeholder, result["plan"], event.get("complexity", ""))
                    elif etype == "progress":
                        for step in result["plan"]:
                            if step.get("id") == event.get("step_id"):
                                step["status"] = event.get("status", step["status"])
                                step["result_summary"] = event.get("summary", "")
                        _render_plan(plan_placeholder, result["plan"])
                    elif etype == "log":
                        result["logs"].append(event.get("message", ""))
                        _render_logs(log_placeholder, result["logs"])
                    elif etype == "relation":
                        data = event.get("data", [])
                        if isinstance(data, list):
                            result["relations"].extend(data)
                    elif etype == "research_ctx":
                        result["research_ctx"] = event.get("data", [])
                    elif etype == "sources":
                        data = event.get("data", [])
                        if isinstance(data, list):
                            result["sources"] = data
                    elif etype == "token":
                        current_answer += event.get("content", "")
                        answer_placeholder.markdown(current_answer + "▌")
                    elif etype == "replan":
                        reason = event.get("reason", "")
                        count = event.get("count", 0)
                        result["replans"].append({"reason": reason, "count": count})
                        current_answer = ""
                        answer_placeholder.empty()
                        answer_container.markdown(f'<div class="replan-banner">🔄 重规划第 {count} 次：{reason}</div>', unsafe_allow_html=True)
                        answer_placeholder = answer_container.empty()
                    elif etype == "done":
                        answer_placeholder.markdown(current_answer)
                        result["answer_parts"].append(current_answer)
                        break
                    elif etype == "error":
                        st.error(event.get("message", "未知错误"))
                        break
    except httpx.TimeoutException:
        st.error("研究超时，请缩小问题范围重试。")
    except httpx.ConnectError:
        st.error("无法连接后端服务。")
    except Exception as exc:
        st.error(f"错误：{exc}")

    return result


if run_clicked and query:
    result = run_research_stream(query, model)
    st.session_state.latest_research_result = result
    st.session_state.latest_research_query = query
    st.session_state.latest_research_model = model

result = st.session_state.latest_research_result
active_query = st.session_state.latest_research_query
active_model = st.session_state.latest_research_model

if result:
    st.divider()

    if result["relations"]:
        st.markdown("### ⚖️ 论文关系发现")
        type_labels = {"contradiction": "🔴 矛盾", "complement": "🟢 互补", "extension": "🔵 延伸", "overlap": "🟡 重叠"}
        for relation in result["relations"]:
            label = type_labels.get(relation.get("relation_type", ""), relation.get("relation_type", ""))
            st.markdown(f'<div class="relation-card"><b>{label}</b><br>{relation.get("summary", "")}</div>', unsafe_allow_html=True)
        st.divider()

    if result["research_ctx"]:
        with st.expander("📊 你的研究状态", expanded=False):
            for item in result["research_ctx"]:
                icon = {"question": "❓", "hypothesis": "🔬", "conclusion": "✅", "direction": "🧭"}.get(item.get("type", ""), "📌")
                st.markdown(f"{icon} **[{item.get('type', '')}]** {item.get('content', '')}")

    if result["sources"]:
        with st.expander(f"📚 参考文献（{len(result['sources'])}）", expanded=True):
            _render_sources(result["sources"])

    st.divider()
    final_answer = result["answer_parts"][-1] if result.get("answer_parts") else ""
    if final_answer:
        default_title = (active_query or "深度研究笔记")[:30]
        note_title = st.text_input("笔记标题", value=default_title, key="rn_title")
        if st.button("💾 保存为研究笔记"):
            conv = f"用户：{active_query}\n\nAI深度研究：\n{final_answer}"
            try:
                response = httpx.post(
                    f"{API_BASE}/notes/save",
                    json={"conversation": conv, "title": note_title, "source_platform": active_model or model},
                    timeout=120.0,
                )
                if response.status_code == 200:
                    saved = response.json()
                    st.success(f"✅ 已保存：{saved.get('title', note_title)}")
                else:
                    detail = "保存失败"
                    try:
                        detail = response.json().get("detail", detail)
                    except Exception:
                        pass
                    st.error(detail)
            except Exception as exc:
                st.error(str(exc))
else:
    st.markdown(
        """
    **深度研究**与普通对话不同——系统会自动执行 Plan-Execute-Replan 流程：

    1. 🧠 **Planner** — 分析问题复杂度，制定研究计划
    2. 🔍 **Researcher** — 执行计划中的每一步（向量检索 + 查预计算关系 + 查研究状态）
    3. ✍️ **Synthesizer** — 基于收集的数据流式生成研究报告
    4. ✅ **Evaluator** — 检查回答质量，不足时自动触发重规划
    5. 🔄 **Replan** — 针对性补充数据或改进回答（最多 2 次）

    适合问**复杂的研究问题**，比如：
    - "调研知识库里关于 XXX 的研究现状"
    - "对比分析知识库中不同方法的优劣"
    - "找出知识库论文之间的矛盾和互补关系"
    """
    )
