"""
ScholarAgent Web 管理面板（Streamlit MVP）

专注于知识管理和可视化，MCP/代理编排不方便处理的场景：
- 批量上传论文/专利
- 查看解析状态和任务进度
- 研究笔记浏览
- 入库质量仪表盘
- Agent 执行链路查看
"""
import os
import streamlit as st

st.set_page_config(
    page_title="ScholarAgent",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📚 ScholarAgent")
st.caption("科研知识管理与 AI 辅助研究系统")

st.markdown("---")

col1, col2, col3 = st.columns(3)

with col1:
    st.metric("📄 文档总数", "0")
with col2:
    st.metric("📝 研究笔记", "0")
with col3:
    st.metric("✅ 解析成功率", "N/A")

st.markdown("---")

st.subheader("快速开始")
st.markdown(
    """
    1. 在左侧菜单选择 **📄 文档管理** 上传论文或专利 PDF
    2. 在 Claude 桌面端配置 MCP Server 连接本系统
    3. 或使用 CLI 工具：`scholar chat "你的问题"`
    4. 讨论结束后保存研究笔记，下次可检索历史讨论

    **接入方式：**
    - **MCP Server** → Claude 桌面端全自动 Tool 调用
    - **代理编排 API** → 任意模型（GPT/Deepseek/Ollama）+ 知识库
    - **CLI 工具** → `scholar chat --model deepseek "问题"`
    """
)

st.markdown("---")
st.subheader("系统状态")

import httpx

API_BASE = os.getenv("API_BASE", "http://backend:8000/api/v1")

try:
    resp = httpx.get(f"{API_BASE}/health", timeout=5.0)
    if resp.status_code == 200:
        data = resp.json()
        st.success(f"后端服务运行正常 | 已加载 {data.get('skills_loaded', 0)} 个 Skill")
    else:
        st.error("后端服务异常")
except Exception:
    st.warning("无法连接后端服务。请确认 backend 已启动（端口 8000）。")
