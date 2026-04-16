"""
Research dashboard.
"""

import os

import httpx
import streamlit as st

st.set_page_config(page_title="研究看板 - ScholarAgent", page_icon="📊", layout="wide")

API_BASE = os.getenv("API_BASE_URL", "http://backend:8000/api/v1")

st.markdown(
    """
<style>
    #MainMenu, footer, header {visibility: hidden;}
    .block-container { padding-top: 1.5rem !important; }
    .notif-card { padding: 10px 14px; margin: 6px 0; border-radius: 8px; background: #eff6ff; border: 1px solid #bfdbfe; font-size: 0.88rem; }
    .state-card { padding: 10px 14px; margin: 5px 0; border-radius: 8px; border: 1px solid #e5e7eb; font-size: 0.88rem; }
    .state-open { border-left: 3px solid #3b82f6; }
    .state-verified { border-left: 3px solid #22c55e; }
    .state-refuted { border-left: 3px solid #ef4444; }
    .state-archived { border-left: 3px solid #9ca3af; opacity: 0.6; }
    .rel-card { padding: 8px 12px; margin: 4px 0; border-radius: 6px; font-size: 0.85rem; background: #fefce8; border: 1px solid #fde68a; }
</style>
""",
    unsafe_allow_html=True,
)

st.markdown("## 📊 研究看板")
st.caption("研究进展追踪 · 论文关系 · 智能提醒")
st.divider()


def load_dashboard():
    try:
        response = httpx.get(f"{API_BASE}/research/dashboard", timeout=10.0)
        if response.status_code == 200:
            return response.json()
    except Exception as exc:
        st.error(f"无法加载看板数据: {exc}")
    return {"research_items": [], "relations": [], "notifications": []}


data = load_dashboard()

notifications = data.get("notifications", [])
if notifications:
    st.markdown("### 🔔 未读通知")
    for notification in notifications:
        nid = notification.get("id", "")
        st.markdown(
            f'<div class="notif-card"><b>{notification.get("title", "")}</b><br><small>{notification.get("detail", "")}</small></div>',
            unsafe_allow_html=True,
        )
        if st.button("标为已读", key=f"read_{nid}"):
            try:
                httpx.post(f"{API_BASE}/research/notifications/{nid}/read", timeout=5.0)
                st.rerun()
            except Exception:
                pass
    st.divider()

items = data.get("research_items", [])
st.markdown("### 📋 研究状态")
if not items:
    st.info("暂无研究状态。使用深度研究功能后，系统会自动追踪你的研究问题和假设。")
else:
    type_icon = {"question": "❓", "hypothesis": "🔬", "conclusion": "✅", "direction": "🧭"}
    open_items = [item for item in items if item.get("status") == "open"]
    other_items = [item for item in items if item.get("status") != "open"]

    if open_items:
        st.markdown("**进行中**")
        for item in open_items:
            icon = type_icon.get(item.get("type", ""), "📌")
            status = item.get("status", "open")
            col1, col2 = st.columns([6, 1])
            with col1:
                st.markdown(
                    f'<div class="state-card state-{status}">{icon} <b>[{item.get("type", "")}]</b> {item.get("content", "")}</div>',
                    unsafe_allow_html=True,
                )
            with col2:
                item_id = item.get("id", "")
                if st.button("归档", key=f"archive_{item_id}"):
                    try:
                        httpx.post(f"{API_BASE}/research/state/{item_id}/status", params={"status": "archived"}, timeout=5.0)
                        st.rerun()
                    except Exception:
                        pass

    if other_items:
        with st.expander(f"已归档 ({len(other_items)})", expanded=False):
            for item in other_items:
                icon = type_icon.get(item.get("type", ""), "📌")
                status = item.get("status", "archived")
                st.markdown(
                    f'<div class="state-card state-{status}">{icon} <b>[{item.get("type", "")}]</b> {item.get("content", "")} <small>({status})</small></div>',
                    unsafe_allow_html=True,
                )

st.divider()

relations = data.get("relations", [])
st.markdown("### ⚖️ 论文关系网络")
if not relations:
    st.info("暂无论文关系。上传论文后，系统会自动检测论文之间的矛盾、互补、延伸等关系。")
else:
    type_labels = {"contradiction": "🔴 矛盾", "complement": "🟢 互补", "extension": "🔵 延伸", "overlap": "🟡 重叠"}
    type_counts = {}
    for relation in relations:
        relation_type = relation.get("relation_type", "other")
        type_counts[relation_type] = type_counts.get(relation_type, 0) + 1

    cols = st.columns(len(type_counts) if type_counts else 1)
    for idx, (relation_type, count) in enumerate(type_counts.items()):
        cols[idx % len(cols)].metric(type_labels.get(relation_type, relation_type), count)

    st.markdown("---")
    for relation in relations:
        relation_type = relation.get("relation_type", "")
        label = type_labels.get(relation_type, relation_type)
        st.markdown(
            f'<div class="rel-card"><b>{label}</b>: {relation.get("summary", "")}<br><small>置信度: {relation.get("confidence", 0):.1%}</small></div>',
            unsafe_allow_html=True,
        )
