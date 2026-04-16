"""
ScholarAgent 对话页面 v4（兼容合并版）

改进：
- 引用来源直接显示在回答下方
- 预设欢迎问题
- Query 重写结果提示
- 保存笔记
- 保留本地与 Docker API_BASE 自动兼容
- 模型列表动态读取后端可用模型
"""

import os
import json

import httpx
import streamlit as st

st.set_page_config(page_title="ScholarAgent", page_icon="🔬", layout="wide")

# Local dev uses localhost, docker container uses backend service name.
default_api_base = "http://localhost:8000/api/v1"
if os.path.exists("/.dockerenv"):
    default_api_base = "http://backend:8000/api/v1"
API_BASE = os.getenv("API_BASE_URL", default_api_base)

# ========================
# 样式
# ========================
st.markdown(
    """
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .block-container { padding-top: 1.2rem !important; padding-bottom: 1rem !important; }

    section[data-testid="stSidebar"] {
        background-color: #f7f7f8;
        border-right: 1px solid #e5e5e5;
    }

    .ref-card {
        padding: 8px 12px;
        margin: 6px 0;
        background: #f8fafc;
        border-left: 3px solid #6366f1;
        border-radius: 0 8px 8px 0;
        font-size: 0.83rem;
        color: #374151;
        line-height: 1.5;
    }
    .ref-card .ref-header {
        font-weight: 600;
        color: #4f46e5;
        margin-bottom: 4px;
    }
    .ref-card .ref-content {
        color: #6b7280;
    }
    .ref-card .ref-score {
        font-size: 0.75rem;
        color: #9ca3af;
    }

    .welcome { text-align: center; padding: 40px 20px; }
    .welcome h2 { color: #1f2937; font-weight: 700; margin-bottom: 4px; }
    .welcome p { color: #6b7280; font-size: 0.95rem; margin-bottom: 24px; }

    .stButton > button {
        border-radius: 20px !important;
    }
</style>
""",
    unsafe_allow_html=True,
)


# ========================
# State
# ========================
if "messages" not in st.session_state:
    st.session_state.messages = []
if "session_id" not in st.session_state:
    st.session_state.session_id = None
if "all_sources" not in st.session_state:
    st.session_state.all_sources = {}
if "model" not in st.session_state:
    st.session_state.model = "qwen3-max-2026-01-23"
if "last_rewrite" not in st.session_state:
    st.session_state.last_rewrite = None
if "pending_prompt" not in st.session_state:
    st.session_state.pending_prompt = None
if "is_streaming" not in st.session_state:
    st.session_state.is_streaming = False
if "last_turn_key" not in st.session_state:
    st.session_state.last_turn_key = None


def new_chat() -> None:
    st.session_state.messages = []
    st.session_state.session_id = None
    st.session_state.all_sources = {}
    st.session_state.last_rewrite = None
    st.session_state.pending_prompt = None
    st.session_state.last_turn_key = None


def load_sessions() -> list[dict]:
    try:
        resp = httpx.get(f"{API_BASE}/proxy/sessions", timeout=5.0)
        return resp.json().get("sessions", []) if resp.status_code == 200 else []
    except Exception:
        return []


def load_session(session_id: str) -> None:
    try:
        resp = httpx.get(f"{API_BASE}/proxy/sessions/{session_id}", timeout=5.0)
        if resp.status_code == 200:
            raw_messages = resp.json().get("messages", [])
            messages: list[dict] = []
            all_sources: dict[int, list[dict]] = {}

            for message in raw_messages:
                if not isinstance(message, dict):
                    continue

                role = message.get("role")
                content = message.get("content", "")
                if role not in ("user", "assistant") or not isinstance(content, str):
                    continue

                msg = {"role": role, "content": content}

                if role == "assistant":
                    sources = message.get("sources")
                    if isinstance(sources, list) and sources:
                        all_sources[len(messages)] = sources

                    rewritten_query = message.get("rewritten_query")
                    if isinstance(rewritten_query, str) and rewritten_query:
                        msg["rewritten_query"] = rewritten_query

                messages.append(msg)

            st.session_state.messages = messages
            st.session_state.session_id = session_id
            st.session_state.all_sources = all_sources
            st.session_state.last_rewrite = None
            st.session_state.pending_prompt = None
            st.session_state.last_turn_key = None
    except Exception:
        pass


def delete_session(session_id: str) -> None:
    try:
        httpx.delete(f"{API_BASE}/proxy/sessions/{session_id}", timeout=5.0)
    except Exception:
        pass


def load_model_options() -> list[str]:
    fallback = [
        "qwen3-max-2026-01-23",
        "qwen3.5-plus",
        "qwen3.5-122b-a10b",
        "qwen-plus",
        "qwen-turbo",
    ]

    try:
        resp = httpx.get(f"{API_BASE}/proxy/models", timeout=4.0)
        if resp.status_code == 200:
            model_items = resp.json().get("models", [])
            seen = set()
            options = []
            for item in model_items:
                model = item.get("model", "")
                if model and model not in seen:
                    seen.add(model)
                    options.append(model)
            if options:
                return options
    except Exception:
        pass

    return fallback


def render_sources(sources: list[dict]) -> None:
    if not sources:
        return

    for i, source in enumerate(sources):
        icon = "📄" if source.get("type") == "paper" else "📝"
        title = source.get("section_title") or source.get("title") or "Unknown"
        score = source.get("score", 0)
        content = source.get("content", "")[:250]

        st.markdown(
            f"""<div class="ref-card">
            <div class="ref-header">{icon} [{i+1}] {title}</div>
            <div class="ref-content">{content}...</div>
            <div class="ref-score">相关度: {score:.2f}</div>
        </div>""",
            unsafe_allow_html=True,
        )


def stream_chat_response(prompt: str, history: list[dict]):
    meta = {
        "sources": [],
        "intent": "knowledge",
        "session_id": st.session_state.session_id,
        "rewritten_query": None,
    }

    try:
        with httpx.Client(timeout=180.0) as client:
            with client.stream(
                "POST",
                f"{API_BASE}/proxy/chat/stream",
                json={
                    "query": prompt,
                    "model": st.session_state.model,
                    "session_id": st.session_state.session_id,
                    "messages": history,
                    "auto_retrieve": True,
                    "retrieve_papers": True,
                    "retrieve_notes": True,
                    "top_k": 5,
                },
            ) as resp:
                if resp.status_code != 200:
                    detail = f"调用失败（{resp.status_code}）"
                    try:
                        payload = resp.json()
                        detail = payload.get("detail", detail)
                    except Exception:
                        if resp.text:
                            detail = resp.text
                    yield f"⚠️ {detail}"
                    return

                for line in resp.iter_lines():
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

                    event_type = event.get("type")

                    if event_type == "meta":
                        meta["sources"] = event.get("sources", []) or []
                        meta["intent"] = event.get("intent", "knowledge")
                        meta["session_id"] = event.get("session_id", meta["session_id"])
                        meta["rewritten_query"] = event.get("rewritten_query")
                    elif event_type == "token":
                        token = event.get("content", "")
                        if token:
                            yield token
                    elif event_type == "done":
                        break

    except httpx.TimeoutException:
        yield "⚠️ 请求超时，请重试。"
    except httpx.ConnectError:
        yield f"⚠️ 无法连接后端，请确认服务已启动（{API_BASE}）。"
    except Exception as e:
        yield f"⚠️ 错误：{e}"
    finally:
        st.session_state._stream_meta = meta


def render_streamed_answer(prompt: str, history: list[dict]) -> str:
    """Render streaming tokens into an isolated placeholder for this assistant message."""
    placeholder = st.empty()
    parts: list[str] = []

    for token in stream_chat_response(prompt, history):
        parts.append(token)
        placeholder.markdown("".join(parts))

    return "".join(parts)


# ========================
# 侧边栏
# ========================
with st.sidebar:
    st.markdown("### 🔬 ScholarAgent")
    st.caption("科研知识助手")

    if st.button("✨ 新建对话", use_container_width=True, type="primary"):
        new_chat()
        st.rerun()

    st.divider()

    if len(st.session_state.messages) >= 2:
        note_title = st.text_input("笔记标题", placeholder="自动生成", label_visibility="collapsed")
        if st.button("💾 保存为笔记", use_container_width=True):
            parts = []
            for message in st.session_state.messages:
                role = "用户" if message["role"] == "user" else "AI"
                parts.append(f"{role}：{message['content']}")

            with st.spinner("总结中..."):
                try:
                    resp = httpx.post(
                        f"{API_BASE}/notes/save",
                        json={
                            "conversation": "\n".join(parts),
                            "title": note_title or "",
                            "source_platform": st.session_state.model,
                        },
                        timeout=60.0,
                    )
                    if resp.status_code == 200:
                        st.success(f"✅ {resp.json()['title']}")
                    else:
                        st.error("保存失败")
                except Exception as e:
                    st.error(str(e))

        st.divider()

    st.markdown("**📂 历史对话**")
    sessions = load_sessions()
    if sessions:
        for session in sessions[:15]:
            session_id = session.get("session_id", "")
            title = session.get("title", "未命名")[:22]
            c1, c2 = st.columns([6, 1])
            with c1:
                active = "▸ " if st.session_state.session_id == session_id else ""
                if st.button(f"{active}{title}", key=f"s_{session_id}", use_container_width=True):
                    load_session(session_id)
                    st.rerun()
            with c2:
                if st.button("×", key=f"d_{session_id}"):
                    delete_session(session_id)
                    if st.session_state.session_id == session_id:
                        new_chat()
                    st.rerun()
    else:
        st.caption("暂无")


# ========================
# 主区域
# ========================
model_options = load_model_options()
if st.session_state.model not in model_options:
    model_options.insert(0, st.session_state.model)

tc = st.columns([1, 5])
with tc[0]:
    st.session_state.model = st.selectbox(
        "m",
        model_options,
        index=model_options.index(st.session_state.model),
        label_visibility="collapsed",
    )

if not st.session_state.messages:
    st.markdown(
        """
    <div class="welcome">
        <h2>🔬 ScholarAgent</h2>
        <p>上传论文到知识库，然后向我提问</p>
    </div>
    """,
        unsafe_allow_html=True,
    )

    st.markdown("")
    preset_cols = st.columns(2)
    presets = [
        ("🔬 你能做什么？", "你能做什么？有什么主要功能？"),
        ("📄 知识库里有什么论文？", "帮我看看知识库里目前有哪些论文，主要涉及哪些研究方向？"),
        ("📝 如何保存研究笔记？", "请介绍一下研究笔记功能，怎么保存和检索历史讨论？"),
        ("🔍 分析论文的创新点", "帮我分析一下知识库里论文的主要创新点和贡献"),
    ]
    for i, (label, query) in enumerate(presets):
        with preset_cols[i % 2]:
            if st.button(label, use_container_width=True, key=f"preset_{i}"):
                # Queue preset query and let the unified send pipeline handle it.
                st.session_state.pending_prompt = query
                st.rerun()


for idx, message in enumerate(st.session_state.messages):
    avatar = "🧑‍🔬" if message["role"] == "user" else "🤖"
    with st.chat_message(message["role"], avatar=avatar):
        st.markdown(message["content"])

        if message["role"] == "assistant" and idx in st.session_state.all_sources:
            sources = st.session_state.all_sources[idx]
            if sources:
                with st.expander(f"📚 {len(sources)} 条引用来源", expanded=False):
                    render_sources(sources)


chat_prompt = st.chat_input("输入你的问题...", disabled=st.session_state.is_streaming)

# Normalize all sends through pending_prompt so the request path is single-entry.
if chat_prompt and not st.session_state.is_streaming:
    st.session_state.pending_prompt = chat_prompt.strip()
    st.rerun()

prompt = st.session_state.pending_prompt

if prompt and not st.session_state.is_streaming:
    prompt = prompt.strip()
    st.session_state.pending_prompt = None

    if prompt:
        # Idempotency guard: same run state + same prompt should only execute once.
        turn_key = f"{st.session_state.session_id or 'new'}|{len(st.session_state.messages)}|{prompt}"
        if st.session_state.last_turn_key != turn_key:
            st.session_state.last_turn_key = turn_key

            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user", avatar="🧑‍🔬"):
                st.markdown(prompt)

            history = [
                {"role": m["role"], "content": m["content"]}
                for m in st.session_state.messages[:-1]
            ]

            st.session_state.is_streaming = True
            with st.chat_message("assistant", avatar="🤖"):
                try:
                    answer = render_streamed_answer(prompt, history)

                    meta = st.session_state.pop("_stream_meta", {})
                    sources = meta.get("sources", [])
                    intent = meta.get("intent", "")
                    rewritten = meta.get("rewritten_query")
                    session_id = meta.get("session_id", st.session_state.session_id)

                    footer = []
                    if intent == "knowledge" and sources:
                        footer.append(f"📚 {len(sources)} 条来源")
                    elif intent == "chat":
                        footer.append("💬 闲聊")
                    elif intent == "intro":
                        footer.append("ℹ️ 功能介绍")
                    if rewritten:
                        footer.append(f"🔄 检索: {rewritten[:40]}")
                    if footer:
                        st.caption(" · ".join(footer))

                    if sources:
                        with st.expander(f"📚 {len(sources)} 条引用来源", expanded=False):
                            render_sources(sources)

                    msg_idx = len(st.session_state.messages)
                    assistant_message = {"role": "assistant", "content": answer}
                    if sources:
                        assistant_message["sources"] = sources
                        st.session_state.all_sources[msg_idx] = sources
                    if rewritten:
                        assistant_message["rewritten_query"] = rewritten

                    st.session_state.messages.append(assistant_message)
                    st.session_state.session_id = session_id
                finally:
                    st.session_state.is_streaming = False
