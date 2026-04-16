"""
深度研究页面

功能：
- 实时显示研究计划和进度
- 流式显示回答
- 展示论文关系
- 展示研究状态
- Replan 过程可视化
"""

import os
import json
import streamlit as st
import httpx

st.set_page_config(page_title="深度研究 - ScholarAgent", page_icon="🔬", layout="wide")

API_BASE = os.getenv("API_BASE_URL", "http://backend:8000/api/v1")

st.markdown("""
<style>
    #MainMenu, footer, header {visibility: hidden;}
    .block-container { padding-top: 1.5rem !important; }

    .step-card { padding: 10px 14px; margin: 5px 0; border-radius: 8px; font-size: 0.88rem; }
    .step-done { background: #f0fdf4; border: 1px solid #86efac; }
    .step-running { background: #eff6ff; border: 1px solid #93c5fd; }
    .step-failed { background: #fef2f2; border: 1px solid #fca5a5; }
    .step-pending { background: #f9fafb; border: 1px solid #e5e7eb; color: #9ca3af; }

    .relation-card { padding: 10px 14px; margin: 5px 0; border-radius: 8px;
                     font-size: 0.85rem; background: #fefce8; border: 1px solid #fde68a; }
    .log-item { font-size: 0.82rem; color: #4b5563; padding: 2px 0; }
    .replan-banner { padding: 8px 14px; margin: 8px 0; border-radius: 8px;
                     background: #fff7ed; border: 1px solid #fed7aa; font-size: 0.85rem; }
</style>
""", unsafe_allow_html=True)


st.markdown("## 🔬 深度研究")
st.caption("Plan-Execute-Replan · 多步自主推理 · 跨论文关系检测")
st.divider()


# ================================================================
# 输入区域
# ================================================================
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


# ================================================================
# 流式研究执行
# ================================================================

def run_research_stream(query: str, model: str):
    """
    调用 SSE 流式研究接口，解析事件并实时展示。

    返回: {
        "plan": [...], "logs": [...], "relations": [...],
        "research_ctx": [...], "answer": "...", "replans": [...]
    }
    """
    result = {
        "plan": [], "logs": [], "relations": [],
        "research_ctx": [], "answer_parts": [], "replans": [],
    }

    # UI 占位符
    plan_container = st.container()
    log_expander = st.expander("📝 执行日志", expanded=True)
    relation_container = st.container()
    answer_container = st.container()

    plan_placeholder = plan_container.empty()
    log_placeholder = log_expander.empty()
    answer_placeholder = answer_container.empty()

    try:
        with httpx.Client(timeout=300.0) as client:
            with client.stream("POST", f"{API_BASE}/research/stream", json={
                "query": query, "model": model,
            }) as resp:
                if resp.status_code != 200:
                    st.error(f"请求失败: {resp.status_code}")
                    return result

                current_answer = ""

                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if not data_str:
                        continue

                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    etype = event.get("type")

                    # ---- 计划 ----
                    if etype == "plan":
                        result["plan"] = event.get("steps", [])
                        complexity = event.get("complexity", "")
                        _render_plan(plan_placeholder, result["plan"], complexity)

                    # ---- 进度 ----
                    elif etype == "progress":
                        sid = event.get("step_id")
                        for s in result["plan"]:
                            if s.get("id") == sid:
                                s["status"] = event.get("status", s["status"])
                                s["result_summary"] = event.get("summary", "")
                        _render_plan(plan_placeholder, result["plan"])

                    # ---- 日志 ----
                    elif etype == "log":
                        msg = event.get("message", "")
                        result["logs"].append(msg)
                        _render_logs(log_placeholder, result["logs"])

                    # ---- 论文关系 ----
                    elif etype == "relation":
                        rels = event.get("data", [])
                        result["relations"].extend(rels)

                    # ---- 研究状态 ----
                    elif etype == "research_ctx":
                        result["research_ctx"] = event.get("data", [])

                    # ---- Token（流式回答）----
                    elif etype == "token":
                        token = event.get("content", "")
                        current_answer += token
                        answer_placeholder.markdown(current_answer + "▌")

                    # ---- Replan ----
                    elif etype == "replan":
                        reason = event.get("reason", "")
                        count = event.get("count", 0)
                        result["replans"].append({"reason": reason, "count": count})
                        current_answer = ""  # 重置回答区域
                        answer_placeholder.empty()
                        answer_container.markdown(
                            f'<div class="replan-banner">'
                            f'🔄 重规划第 {count} 次：{reason}</div>',
                            unsafe_allow_html=True,
                        )
                        answer_placeholder = answer_container.empty()

                    # ---- 完成 ----
                    elif etype == "done":
                        answer_placeholder.markdown(current_answer)
                        result["answer_parts"].append(current_answer)
                        break

                    # ---- 错误 ----
                    elif etype == "error":
                        st.error(event.get("message", "未知错误"))
                        break

    except httpx.TimeoutException:
        st.error("研究超时，请缩小问题范围重试。")
    except httpx.ConnectError:
        st.error("无法连接后端服务。")
    except Exception as e:
        st.error(f"错误：{e}")

    return result


def _render_plan(placeholder, plan: list, complexity: str = ""):
    """渲染计划步骤"""
    if not plan:
        return

    action_icons = {
        "retrieve": "🔍", "lookup_relations": "⚖️",
        "lookup_state": "📊", "synthesize": "✍️",
    }
    status_icons = {"done": "✅", "running": "🔄", "failed": "❌", "pending": "⏳"}

    parts = []
    if complexity:
        parts.append(f"**📋 研究计划** · 复杂度: `{complexity}`\n")

    for s in plan:
        status = s.get("status", "pending")
        si = status_icons.get(status, "❓")
        ai = action_icons.get(s.get("action", ""), "📌")
        summary = s.get("result_summary", "")
        summary_text = f" — {summary}" if summary else ""

        parts.append(
            f'<div class="step-card step-{status}">'
            f'{si} {ai} <b>Step {s.get("id", "?")}</b>: '
            f'{s.get("action", "")}{summary_text}</div>'
        )

    placeholder.markdown("\n".join(parts), unsafe_allow_html=True)


def _render_logs(placeholder, logs: list):
    """渲染执行日志"""
    parts = [f'<div class="log-item">{log}</div>' for log in logs[-15:]]
    placeholder.markdown("\n".join(parts), unsafe_allow_html=True)


# ================================================================
# 执行入口
# ================================================================
if run_clicked and query:
    result = run_research_stream(query, model)

    st.divider()

    # 显示论文关系
    if result["relations"]:
        st.markdown("### ⚖️ 论文关系发现")
        type_labels = {
            "contradiction": "🔴 矛盾", "complement": "🟢 互补",
            "extension": "🔵 延伸", "overlap": "🟡 重叠",
        }
        for r in result["relations"]:
            rtype = r.get("relation_type", "")
            label = type_labels.get(rtype, rtype)
            st.markdown(
                f'<div class="relation-card"><b>{label}</b><br>'
                f'{r.get("summary", "")}</div>',
                unsafe_allow_html=True,
            )
        st.divider()

    # 显示研究状态
    if result["research_ctx"]:
        with st.expander("📊 你的研究状态", expanded=False):
            for item in result["research_ctx"]:
                itype = item.get("type", "")
                icon = {"question": "❓", "hypothesis": "🔬",
                        "conclusion": "✅", "direction": "🧭"}.get(itype, "📌")
                st.markdown(f"{icon} **[{itype}]** {item.get('content', '')}")

    # 保存为笔记
    st.divider()
    final_answer = result["answer_parts"][-1] if result["answer_parts"] else ""
    if final_answer:
        note_title = st.text_input("笔记标题", value=query[:30], key="rn_title")
        if st.button("💾 保存为研究笔记"):
            conv = f"用户：{query}\n\nAI深度研究：\n{final_answer}"
            try:
                r = httpx.post(f"{API_BASE}/notes/save", json={
                    "conversation": conv, "title": note_title,
                    "source_platform": model,
                }, timeout=60.0)
                if r.status_code == 200:
                    st.success(f"✅ 已保存")
                else:
                    st.error("保存失败")
            except Exception as e:
                st.error(str(e))

elif not run_clicked:
    st.markdown("""
    **深度研究**与普通对话不同——系统会自动执行 Plan-Execute-Replan 流程：

    1. 🧠 **Planner** — 分析问题复杂度，制定研究计划（规则驱动，毫秒级）
    2. 🔍 **Researcher** — 执行计划中的每一步（向量检索 + 查预计算关系 + 查研究状态）
    3. ✍️ **Synthesizer** — 基于收集的数据流式生成研究报告
    4. ✅ **Evaluator** — 检查回答质量，不足时自动触发重规划
    5. 🔄 **Replan** — 针对性补充数据或改进回答（最多 2 次）

    适合问**复杂的研究问题**，比如：
    - "调研知识库里关于 XXX 的研究现状"
    - "对比分析知识库中不同方法的优劣"
    - "找出知识库论文之间的矛盾和互补关系"
    """)
