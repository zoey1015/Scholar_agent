"""
文档管理页面：上传、列表、检索测试
"""
import os
import streamlit as st
import httpx

st.title("📄 文档管理")

API_BASE = os.getenv("API_BASE", "http://backend:8000/api/v1")

# ========================
# 上传区域
# ========================
st.subheader("上传文档")
uploaded_file = st.file_uploader("选择 PDF 文件", type=["pdf"], accept_multiple_files=False)

if uploaded_file and st.button("上传并解析"):
    with st.spinner("上传中..."):
        try:
            resp = httpx.post(
                f"{API_BASE}/documents/upload",
                files={"file": (uploaded_file.name, uploaded_file.getvalue(), "application/pdf")},
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
    doc_type = st.selectbox("文档类型", ["all", "paper", "patent"])

if query and st.button("检索"):
    with st.spinner("检索中..."):
        try:
            resp = httpx.post(
                f"{API_BASE}/documents/search",
                json={"query": query, "top_k": top_k, "doc_type": doc_type},
                timeout=30.0,
            )
            data = resp.json()
            results = data.get("results", [])
            if results:
                for i, r in enumerate(results):
                    with st.expander(f"[{i+1}] {r.get('section_title', 'N/A')}"):
                        st.write(r.get("content", ""))
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
            st.write(f"- **{doc.get('title', 'Untitled')}** ({doc.get('doc_type')}, {doc.get('language')})")
    else:
        st.info("暂无文档。请先上传 PDF 文件。")
except Exception:
    st.warning("无法加载文档列表。")
