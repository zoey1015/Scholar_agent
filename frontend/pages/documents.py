"""
文档管理页面
"""

import os
import streamlit as st
import httpx

st.title("📄 文档管理")

default_api_base = "http://localhost:8000/api/v1"
if os.path.exists("/.dockerenv"):
    default_api_base = "http://backend:8000/api/v1"
API_BASE = os.getenv("API_BASE_URL", default_api_base)

# ========================
# 上传区域
# ========================
st.subheader("上传文档")

col_upload1, col_upload2 = st.columns([2, 1])
with col_upload1:
    uploaded_file = st.file_uploader("选择 PDF 文件", type=["pdf"])
with col_upload2:
    doc_type = st.selectbox("文档类型", ["paper", "patent"])
    language = st.selectbox("语言", ["en", "zh", "mixed"])

if uploaded_file and st.button("上传并解析", type="primary"):
    with st.spinner("上传中..."):
        try:
            resp = httpx.post(
                f"{API_BASE}/documents/upload",
                files={"file": (uploaded_file.name, uploaded_file.getvalue(), "application/pdf")},
                params={"doc_type": doc_type, "language": language},
                timeout=60.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                st.success(f"上传成功！文档 ID: {data['document_id']}")
                st.info(f"解析任务 ID: {data['task_id']}，状态: {data['status']}")
            else:
                st.error(f"上传失败: {resp.text}")
        except Exception as e:
            st.error(f"连接错误: {e}")

st.markdown("---")

# ========================
# 检索测试
# ========================
st.subheader("🔍 知识库检索")
query = st.text_input("输入检索关键词")
col1, col2 = st.columns(2)
with col1:
    top_k = st.slider("返回数量", 1, 20, 5)
with col2:
    search_doc_type = st.selectbox("筛选类型", ["all", "paper", "patent"], key="search_type")

if query and st.button("检索"):
    with st.spinner("检索中..."):
        try:
            resp = httpx.post(
                f"{API_BASE}/documents/search",
                json={"query": query, "top_k": top_k, "doc_type": search_doc_type},
                timeout=30.0,
            )
            data = resp.json()
            results = data.get("results", [])
            if results:
                for i, r in enumerate(results):
                    score = r.get("score", 0)
                    with st.expander(f"[{i+1}] {r.get('section_title', 'N/A')} (相关度: {score:.3f})"):
                        st.write(r.get("content", ""))
                        st.caption(f"文档ID: {r.get('document_id', '')}")
            else:
                st.info("未找到相关结果。")
        except Exception as e:
            st.error(f"检索错误: {e}")

st.markdown("---")

# ========================
# 文档列表
# ========================
st.subheader("📋 文档列表")
try:
    resp = httpx.get(f"{API_BASE}/documents", timeout=10.0)
    data = resp.json()
    docs = data.get("documents", [])
    if docs:
        for doc in docs:
            status_emoji = {"ready": "✅", "success": "✅", "pending": "⏳", "processing": "🔄", "failed": "❌"}.get(doc.get("parse_status"), "❓")
            with st.expander(f"{status_emoji} {doc.get('title', 'Untitled')} ({doc.get('doc_type')})"):
                st.write(f"**语言:** {doc.get('language')} | **状态:** {doc.get('parse_status')}")
                if doc.get("authors"):
                    names = [a.get("name", "") for a in doc["authors"] if isinstance(a, dict)]
                    st.write(f"**作者:** {', '.join(names)}")
                if doc.get("tags"):
                    st.write(f"**关键词:** {', '.join(doc['tags'])}")
                if doc.get("quality_score"):
                    qs = doc["quality_score"]
                    st.write(f"**质量评分:** {qs.get('overall_score', 'N/A')}/5")
                st.caption(f"ID: {doc.get('id')} | 创建: {(doc.get('created_at') or '')[:19]}")
    else:
        st.info("暂无文档。请上传 PDF 文件。")
except Exception:
    st.warning("无法加载文档列表。")
