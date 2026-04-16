"""
研究笔记页面

浏览、检索、查看和删除结构化研究笔记。
"""

import os
import streamlit as st
import httpx

st.title("📝 研究笔记")

default_api_base = "http://localhost:8000/api/v1"
if os.path.exists("/.dockerenv"):
    default_api_base = "http://backend:8000/api/v1"
API_BASE = os.getenv("API_BASE_URL", default_api_base)

# ========================
# 笔记检索
# ========================
st.subheader("🔍 检索笔记")
search_query = st.text_input("输入关键词搜索历史笔记")

if search_query and st.button("检索"):
    with st.spinner("检索中..."):
        try:
            resp = httpx.post(
                f"{API_BASE}/notes/search",
                json={"query": search_query, "top_k": 10},
                timeout=30.0,
            )
            data = resp.json()
            results = data.get("results", [])
            if results:
                for i, r in enumerate(results):
                    score = r.get("score", 0)
                    source = r.get("source", "")
                    with st.expander(f"[{i+1}] {r.get('title', '未命名')} (相关度: {score:.3f}, {source})"):
                        st.write(r.get("summary", ""))
                        st.caption(f"笔记ID: {r.get('note_id', '')}")
            else:
                st.info(f"未找到与「{search_query}」相关的笔记。")
        except Exception as e:
            st.error(f"检索错误: {e}")

st.markdown("---")

# ========================
# 笔记列表
# ========================
st.subheader("📋 所有笔记")

try:
    resp = httpx.get(f"{API_BASE}/notes", params={"page_size": 50}, timeout=10.0)
    data = resp.json()
    notes = data.get("notes", [])
    total = data.get("total", 0)

    if notes:
        st.caption(f"共 {total} 条笔记")

        for note in notes:
            title = note.get("title", "未命名")
            created = (note.get("created_at") or "")[:10]
            platform = note.get("source_platform", "")

            with st.expander(f"📝 {title}  [{created}]  {platform}"):
                # 摘要
                if note.get("summary"):
                    st.markdown(f"**概述：** {note['summary']}")

                # 结构化字段
                col1, col2 = st.columns(2)

                with col1:
                    if note.get("innovations"):
                        st.markdown("**💡 创新点**")
                        for item in note["innovations"]:
                            st.write(f"• {item}")

                    if note.get("key_questions"):
                        st.markdown("**❓ 核心问题**")
                        for item in note["key_questions"]:
                            st.write(f"• {item}")

                    if note.get("hypotheses"):
                        st.markdown("**🔬 关键假设**")
                        for item in note["hypotheses"]:
                            st.write(f"• {item}")

                with col2:
                    if note.get("conclusions"):
                        st.markdown("**🎯 主要结论**")
                        for item in note["conclusions"]:
                            st.write(f"• {item}")

                    if note.get("experiments_todo"):
                        st.markdown("**🧪 待验证实验**")
                        for item in note["experiments_todo"]:
                            st.write(f"• {item}")

                # 元信息
                st.caption(
                    f"来源: {note.get('source_type', 'N/A')} | "
                    f"平台: {platform or 'N/A'} | "
                    f"ID: {note.get('note_id', '')}"
                )

                # 删除按钮
                if st.button(f"🗑️ 删除", key=f"del_{note.get('note_id')}"):
                    try:
                        del_resp = httpx.delete(
                            f"{API_BASE}/notes/{note['note_id']}",
                            timeout=10.0,
                        )
                        if del_resp.status_code == 200:
                            st.success("已删除")
                            st.rerun()
                        else:
                            st.error("删除失败")
                    except Exception as e:
                        st.error(f"错误: {e}")
    else:
        st.info("暂无研究笔记。在「💬 智能对话」中讨论后，点击侧边栏的「保存笔记」来创建。")

except Exception as e:
    st.warning(f"无法加载笔记列表: {e}")
