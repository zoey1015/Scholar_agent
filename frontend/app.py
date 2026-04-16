"""
ScholarAgent 主页（v3 合并版）
"""

import os
import streamlit as st
import httpx

st.set_page_config(page_title="ScholarAgent", page_icon="🔬", layout="wide")

# Local dev uses localhost, docker container uses backend service name.
default_api_base = "http://localhost:8000/api/v1"
if os.path.exists("/.dockerenv"):
    default_api_base = "http://backend:8000/api/v1"
API_BASE = os.getenv("API_BASE_URL", default_api_base)

st.markdown(
    """
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .block-container { padding-top: 2rem !important; }
</style>
""",
    unsafe_allow_html=True,
)

st.markdown("# 🔬 ScholarAgent")
st.caption("科研知识管理与 AI 辅助研究系统")

st.divider()

try:
    docs = httpx.get(f"{API_BASE}/documents", timeout=5.0).json()
    notes = httpx.get(f"{API_BASE}/notes", timeout=5.0).json()
    sessions = httpx.get(f"{API_BASE}/proxy/sessions", timeout=5.0).json()

    c1, c2, c3 = st.columns(3)
    c1.metric("📄 知识库文档", docs.get("total", 0))
    c2.metric("📝 研究笔记", notes.get("total", 0))
    c3.metric("💬 对话记录", len(sessions.get("sessions", [])))
except Exception:
    st.info("后端连接中...")

st.divider()

st.markdown(
    """
**💬 智能对话** — 左侧选择 chat，直接和 AI 对话，系统自动检索知识库

**📄 文档管理** — 左侧选择 documents，上传论文 PDF

**📝 研究笔记** — 左侧选择 notes，浏览结构化笔记
"""
)
