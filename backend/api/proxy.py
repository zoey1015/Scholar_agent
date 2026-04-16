"""
代理编排 API v4（兼容合并版）

能力：
1. Query 重写：中文问题扩展英文检索关键词
2. 并发检索：论文与笔记并发
3. 上下文压缩：长对话压缩摘要
4. 意图分流：chat / intro / knowledge
5. 会话持久化：PostgreSQL
"""

import asyncio
import json
import logging
import re
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.llm_adapters.base import list_available_models, resolve_adapter
from backend.skills.base import SkillContext
from backend.skills.registry import skill_registry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/proxy", tags=["proxy"])

MOCK_USER_ID = "00000000-0000-0000-0000-000000000001"

# ========================
# System Prompts
# ========================

SELF_INTRO = """我是 ScholarAgent，一个科研知识助手。我的核心能力包括：
1. 论文检索与分析：从你上传的论文知识库中检索相关内容
2. 研究笔记管理：保存对话为结构化笔记，随时可检索
3. 学术问答：基于知识库内容回答研究问题
4. 对话记忆：记住本次对话上下文，支持多轮深入讨论"""

RAG_SYSTEM = f"""你是 ScholarAgent，一个专业的科研知识助手。

{SELF_INTRO}

你的知识仅来源于用户上传到知识库的论文和研究笔记。

回答规范：
- 基于参考资料回答时，在句末用 [1] [2] 标注来源编号
- 多条资料支持同一观点时，合并标注如 [1][3]
- 如果知识库中没有相关内容，坦诚说“目前知识库中没有相关信息，建议上传相关论文”
- 不要编造论文标题、作者或实验数据
- 保持回答简洁有条理，使用与用户相同的语言
- 在多轮对话中，主动关联之前讨论过的内容，保持话题连贯"""

RAG_SYSTEM_WITH_CONTEXT = RAG_SYSTEM + """

===== 检索到的参考资料 =====
{context}
===========================

{history_summary}"""

CHAT_SYSTEM = f"""你是 ScholarAgent，一个科研知识助手。

{SELF_INTRO}

用户现在在进行日常对话。友好地回应，并适当引导用户使用你的学术研究功能。"""

UNSUPPORTED_RESPONSE = """这个问题超出了我目前的能力范围。

我是 ScholarAgent，专注于帮你管理和利用学术知识。我可以：
- 检索你上传的论文，回答相关问题
- 把重要讨论保存为结构化笔记
- 跨论文对比和分析

试试问我关于你研究领域的问题吧。"""

QUERY_REWRITE_PROMPT = """你是一个学术检索查询优化器。用户用中文提问，但知识库中的论文是英文的。

请把用户的问题改写为最适合检索英文学术论文的查询。要求：
1. 翻译核心术语为英文学术术语
2. 提取 2-3 个关键检索短语
3. 保留原始中文查询作为备选

输出格式（只输出 JSON，不要其他内容）：
{"en_queries": ["english query 1", "english query 2"], "zh_query": "优化后的中文查询"}"""

COMPRESS_PROMPT = """请用 2-3 句话总结以下对话的核心内容和关键结论，保留重要的术语和数据：

{conversation}

只输出总结，不要其他内容。"""


# ========================
# Data Models
# ========================

class Message(BaseModel):
    role: str
    content: str


class ProxyChatRequest(BaseModel):
    query: str
    model: str = ""
    session_id: Optional[str] = None
    messages: Optional[list[Message]] = []
    auto_retrieve: bool = True
    retrieve_papers: bool = True
    retrieve_notes: bool = True
    top_k: int = 5
    doc_type: str = "all"
    auto_save_note: bool = False
    temperature: float = 0.7
    max_tokens: int = 4096


class SourceItem(BaseModel):
    type: str
    chunk_id: Optional[str] = None
    note_id: Optional[str] = None
    document_id: Optional[str] = None
    section_title: Optional[str] = None
    title: Optional[str] = None
    content: str
    score: float


class ProxyChatResponse(BaseModel):
    answer: str
    model_used: str
    sources: list[SourceItem] = []
    note_id: Optional[str] = None
    session_id: str
    intent: str = "knowledge"
    rewritten_query: Optional[str] = None


# ========================
# Chat History Service
# ========================

class ChatHistoryService:
    def save_session(self, session_id: str, user_id: str, title: str, messages: list[dict]) -> None:
        from backend.db.postgres import SyncSession
        from sqlalchemy import text

        db = SyncSession()
        try:
            db.execute(
                text(
                    """
                INSERT INTO chat_sessions (id, user_id, title, messages, updated_at)
                VALUES (:id, :user_id, :title, :messages, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    messages = :messages,
                    title = :title,
                    updated_at = NOW()
                """
                ),
                {
                    "id": session_id,
                    "user_id": user_id,
                    "title": title,
                    "messages": json.dumps(messages, ensure_ascii=False),
                },
            )
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning(f"Save session failed: {e}")
        finally:
            db.close()

    def load_session(self, session_id: str) -> list[dict]:
        from backend.db.postgres import SyncSession
        from sqlalchemy import text

        db = SyncSession()
        try:
            result = db.execute(
                text("SELECT messages FROM chat_sessions WHERE id = :id"),
                {"id": session_id},
            ).fetchone()
            if result and result[0]:
                return json.loads(result[0]) if isinstance(result[0], str) else result[0]
            return []
        except Exception as e:
            logger.warning(f"Load session failed: {e}")
            return []
        finally:
            db.close()

    def list_sessions(self, user_id: str, limit: int = 30) -> list[dict]:
        from backend.db.postgres import SyncSession
        from sqlalchemy import text

        db = SyncSession()
        try:
            results = db.execute(
                text(
                    """
                SELECT id, title, updated_at
                FROM chat_sessions
                WHERE user_id = :user_id
                ORDER BY updated_at DESC
                LIMIT :limit
                """
                ),
                {"user_id": user_id, "limit": limit},
            ).fetchall()

            return [
                {
                    "session_id": str(r[0]),
                    "title": r[1],
                    "updated_at": r[2].isoformat() if r[2] else None,
                }
                for r in results
            ]
        except Exception as e:
            logger.warning(f"List sessions failed: {e}")
            return []
        finally:
            db.close()

    def delete_session(self, session_id: str) -> bool:
        from backend.db.postgres import SyncSession
        from sqlalchemy import text

        db = SyncSession()
        try:
            result = db.execute(
                text("DELETE FROM chat_sessions WHERE id = :id"),
                {"id": session_id},
            )
            db.commit()
            return result.rowcount > 0
        except Exception as e:
            db.rollback()
            logger.warning(f"Delete session failed: {e}")
            return False
        finally:
            db.close()

    def ensure_table(self) -> None:
        from backend.db.postgres import SyncSession
        from sqlalchemy import text

        db = SyncSession()
        try:
            db.execute(
                text(
                    """
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id VARCHAR(36) PRIMARY KEY,
                    user_id VARCHAR(36) NOT NULL,
                    title VARCHAR(200) DEFAULT '',
                    messages JSONB DEFAULT '[]',
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
                """
                )
            )
            db.execute(
                text(
                    """
                CREATE INDEX IF NOT EXISTS idx_chat_sessions_user
                ON chat_sessions(user_id, updated_at DESC)
                """
                )
            )
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning(f"Create chat_sessions table failed: {e}")
        finally:
            db.close()


_chat_history: Optional[ChatHistoryService] = None


def get_chat_history() -> ChatHistoryService:
    global _chat_history
    if _chat_history is None:
        _chat_history = ChatHistoryService()
        _chat_history.ensure_table()
    return _chat_history


# ========================
# Intent Routing
# ========================

def classify_intent(query: str) -> str:
    q = query.strip().lower()

    if len(q) < 4:
        return "chat"

    chat_exact = {
        "你好",
        "hello",
        "hi",
        "hey",
        "嗨",
        "早上好",
        "晚上好",
        "下午好",
        "谢谢",
        "thanks",
        "再见",
        "bye",
        "ok",
        "好的",
        "嗯",
        "哦",
        "测试",
        "test",
        "你是谁",
        "你叫什么",
    }
    if q in chat_exact:
        return "chat"

    # Domain/academic questions should stay in knowledge path even if wording includes
    # phrases like "介绍一下" that could otherwise be routed to intro.
    academic_keywords = [
        "论文",
        "paper",
        "研究",
        "方法",
        "算法",
        "模型",
        "实验",
        "神经网络",
        "李雅普诺夫",
        "lyapunov",
        "稳定性",
        "检索",
        "知识库",
        "笔记",
    ]
    for keyword in academic_keywords:
        if keyword in q:
            return "knowledge"

    intro_patterns = [
        "你能做什么",
        "你有什么功能",
        "怎么用",
        "如何使用",
        "主要功能",
        "你会什么",
        "介绍一下",
        "what can you do",
        "help",
        "帮助",
    ]
    for pattern in intro_patterns:
        if pattern in q:
            return "intro"

    return "knowledge"


# ========================
# Query Rewrite & Compression
# ========================

async def rewrite_query(query: str, adapter, model: str) -> dict:
    has_chinese = any("\u4e00" <= c <= "\u9fff" for c in query)
    if not has_chinese:
        return {"en_queries": [query], "zh_query": query}

    try:
        from backend.config import get_settings

        settings = get_settings()
        light_model = settings.light_llm_model
        try:
            light_adapter, light_model_name = resolve_adapter(light_model)
        except ValueError:
            light_adapter, light_model_name = adapter, model

        response = await light_adapter.chat(
            model=light_model_name,
            messages=[{"role": "user", "content": f"用户查询：{query}"}],
            system=QUERY_REWRITE_PROMPT,
            temperature=0.1,
            max_tokens=300,
        )

        text = response.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            data = json.loads(match.group())
            en_queries = data.get("en_queries", [query])
            zh_query = data.get("zh_query", query)
            if not isinstance(en_queries, list) or not en_queries:
                en_queries = [query]
            return {"en_queries": en_queries, "zh_query": zh_query}
    except Exception as e:
        logger.warning(f"Query rewrite failed: {e}")

    return {"en_queries": [query], "zh_query": query}


async def rewrite_query_with_timeout(
    query: str,
    adapter,
    model: str,
    timeout_seconds: float = 3.0,
) -> dict:
    try:
        return await asyncio.wait_for(
            rewrite_query(query, adapter, model),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.warning("Query rewrite timed out, using original query")
        return {"en_queries": [query], "zh_query": query}
    except Exception as e:
        logger.warning(f"Query rewrite wrapper failed: {e}")
        return {"en_queries": [query], "zh_query": query}


async def compress_history(messages: list[dict], adapter, model: str) -> str:
    if len(messages) <= 8:
        return ""

    old_messages = messages[:-4]
    conv_text = "\n".join(
        f"{'用户' if m['role'] == 'user' else 'AI'}：{m['content'][:200]}"
        for m in old_messages
    )

    try:
        from backend.config import get_settings

        settings = get_settings()
        light_model = settings.light_llm_model
        try:
            light_adapter, light_model_name = resolve_adapter(light_model)
        except ValueError:
            light_adapter, light_model_name = adapter, model

        summary = await light_adapter.chat(
            model=light_model_name,
            messages=[
                {
                    "role": "user",
                    "content": COMPRESS_PROMPT.format(conversation=conv_text[:2000]),
                }
            ],
            system="",
            temperature=0.2,
            max_tokens=300,
        )
        return f"[之前对话摘要] {summary.strip()}"
    except Exception as e:
        logger.warning(f"History compression failed: {e}")
        return ""


# ========================
# Concurrent Retrieval
# ========================

async def concurrent_retrieve(
    queries: list[str],
    user_id: str,
    retrieve_papers: bool,
    retrieve_notes: bool,
    top_k: int,
    doc_type: str,
) -> tuple[list[dict], list[dict]]:
    async def _retrieve_papers() -> list[dict]:
        if not retrieve_papers:
            return []
        skill = skill_registry.get("retrieval")
        if not skill:
            return []

        all_results: list[dict] = []
        seen_chunks: set[str] = set()
        for query in queries[:3]:
            ctx = SkillContext(
                user_id=user_id,
                query=query,
                metadata={"top_k": top_k, "doc_type": doc_type},
            )
            result = await skill.execute(ctx)
            if result.data:
                for item in result.data.get("results", []):
                    chunk_id = item.get("chunk_id", "")
                    if chunk_id not in seen_chunks:
                        seen_chunks.add(chunk_id)
                        all_results.append(item)

        all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return all_results[:top_k]

    async def _retrieve_notes() -> list[dict]:
        if not retrieve_notes:
            return []
        try:
            from concurrent.futures import ThreadPoolExecutor

            from backend.services.notes_service import get_notes_service

            main_query = queries[0] if queries else ""

            def _search() -> list[dict]:
                return get_notes_service().search_notes(
                    user_id=user_id,
                    query=main_query,
                    top_k=3,
                )

            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                return await loop.run_in_executor(pool, _search)
        except Exception as e:
            logger.warning(f"Notes retrieval failed: {e}")
            return []

    paper_results, note_results = await asyncio.gather(
        _retrieve_papers(),
        _retrieve_notes(),
    )
    return paper_results, note_results


# ========================
# Helpers
# ========================

def _save_to_history(
    session_id: str,
    query: str,
    answer: str,
    prev_messages: list[dict],
    sources: Optional[list] = None,
    rewritten_query: Optional[str] = None,
) -> None:
    try:
        all_messages = list(prev_messages)
        all_messages.append({"role": "user", "content": query})

        assistant_message = {"role": "assistant", "content": answer}
        if sources:
            normalized_sources = []
            for source in sources:
                if hasattr(source, "model_dump"):
                    normalized_sources.append(source.model_dump())
                elif isinstance(source, dict):
                    normalized_sources.append(source)
            if normalized_sources:
                assistant_message["sources"] = normalized_sources

        if rewritten_query:
            assistant_message["rewritten_query"] = rewritten_query

        all_messages.append(assistant_message)
        title = query[:30] + ("..." if len(query) > 30 else "")
        get_chat_history().save_session(session_id, MOCK_USER_ID, title, all_messages)
    except Exception as e:
        logger.warning(f"Save history failed: {e}")


async def _auto_save_note(messages: list[dict], query: str, answer: str, model: str) -> Optional[str]:
    try:
        skill = skill_registry.get("conversation_summary")
        if not skill:
            return None

        conv_parts = [
            f"{'用户' if m['role'] == 'user' else 'AI'}：{m['content']}"
            for m in messages[-6:]
        ]
        conv_parts.append(f"用户：{query}")
        conv_parts.append(f"AI：{answer}")

        ctx = SkillContext(
            user_id=MOCK_USER_ID,
            metadata={
                "conversation": "\n".join(conv_parts),
                "source_platform": model,
            },
        )
        result = await skill.execute(ctx)
        if result.data:
            from concurrent.futures import ThreadPoolExecutor

            def _save() -> str:
                from backend.services.notes_service import get_notes_service

                return get_notes_service().save_note(MOCK_USER_ID, result.data, "proxy")

            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                return await loop.run_in_executor(pool, _save)
    except Exception as e:
        logger.warning(f"Auto save note failed: {e}")
    return None


def _sse_event(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ========================
# Main API
# ========================

@router.post("/chat", response_model=ProxyChatResponse)
async def proxy_chat(req: ProxyChatRequest):
    from backend.config import get_settings

    settings = get_settings()
    model = req.model or settings.default_llm_model
    session_id = req.session_id or str(uuid.uuid4())
    sources: list[SourceItem] = []

    try:
        adapter, model_name = resolve_adapter(model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    intent = classify_intent(req.query)

    if intent == "intro":
        answer = f"""你好！{SELF_INTRO}

使用方式：
- 直接提问学术问题，我会自动检索知识库里的论文
- 讨论结束后，在侧边栏点“保存为笔记”，我会自动提炼要点
- 下次提问时，之前保存的笔记也会被检索到

试试这些问题：
- 帮我分析这篇论文的创新点
- XXX 方法和 YYY 方法有什么区别？
- 总结一下知识库里关于 ZZZ 的研究进展"""
        _save_to_history(session_id, req.query, answer, [])
        return ProxyChatResponse(
            answer=answer,
            model_used=model,
            sources=[],
            session_id=session_id,
            intent="intro",
        )

    if intent == "chat":
        try:
            answer = await adapter.chat(
                model=model_name,
                messages=[{"role": "user", "content": req.query}],
                system=CHAT_SYSTEM,
                temperature=0.8,
                max_tokens=500,
            )
        except Exception:
            answer = "你好！我是 ScholarAgent，有什么学术问题我可以帮你的吗？"

        _save_to_history(session_id, req.query, answer, [])
        return ProxyChatResponse(
            answer=answer,
            model_used=model,
            sources=[],
            session_id=session_id,
            intent="chat",
        )

    if intent == "unsupported":
        _save_to_history(session_id, req.query, UNSUPPORTED_RESPONSE, [])
        return ProxyChatResponse(
            answer=UNSUPPORTED_RESPONSE,
            model_used=model,
            sources=[],
            session_id=session_id,
            intent="unsupported",
        )

    messages_full: list[dict] = []
    if req.messages:
        messages_full = [{"role": m.role, "content": m.content} for m in req.messages]
    elif req.session_id:
        messages_full = get_chat_history().load_session(req.session_id)

    # Keep only role/content fields for model context to avoid provider schema issues.
    messages_for_llm = [
        {
            "role": message.get("role", "user"),
            "content": message.get("content", ""),
        }
        for message in messages_full
        if isinstance(message, dict) and message.get("content")
    ]

    rewrite_task = rewrite_query_with_timeout(req.query, adapter, model_name, timeout_seconds=3.0)
    compress_task = compress_history(messages_for_llm, adapter, model_name)
    rewritten, history_summary = await asyncio.gather(rewrite_task, compress_task)

    search_queries = [req.query]
    for query in rewritten.get("en_queries", []):
        if query and query not in search_queries:
            search_queries.append(query)

    rewritten_display = " / ".join(rewritten.get("en_queries", []))

    paper_results: list[dict] = []
    note_results: list[dict] = []
    if req.auto_retrieve:
        paper_results, note_results = await concurrent_retrieve(
            queries=search_queries,
            user_id=MOCK_USER_ID,
            retrieve_papers=req.retrieve_papers,
            retrieve_notes=req.retrieve_notes,
            top_k=req.top_k,
            doc_type=req.doc_type,
        )

    context_parts: list[str] = []
    ref_index = 1

    for result in paper_results:
        content = result.get("content", "")
        section = result.get("section_title", "")
        score = result.get("score", 0)
        if score < 0.25:
            continue

        label = f"[{ref_index}]"
        if section:
            label += f" ({section})"
        context_parts.append(f"{label}\n{content[:600]}")

        sources.append(
            SourceItem(
                type="paper",
                chunk_id=result.get("chunk_id"),
                document_id=result.get("document_id"),
                section_title=section,
                content=content[:300],
                score=score,
            )
        )
        ref_index += 1

    for note in note_results:
        summary = note.get("summary", "")
        title = note.get("title", "")
        score = note.get("score", 0)
        if score < 0.25 or not summary:
            continue

        label = f"[{ref_index}]"
        if title:
            label += f" (笔记：{title})"
        context_parts.append(f"{label}\n{summary[:400]}")

        sources.append(
            SourceItem(
                type="note",
                note_id=note.get("note_id"),
                title=title,
                content=summary[:300],
                score=score,
            )
        )
        ref_index += 1

    if context_parts:
        context_text = "\n\n".join(context_parts)
        history_section = f"\n[对话历史摘要] {history_summary}" if history_summary else ""
        system = RAG_SYSTEM_WITH_CONTEXT.format(
            context=context_text,
            history_summary=history_section,
        )
    else:
        system = RAG_SYSTEM
        if history_summary:
            system += f"\n\n[对话历史摘要] {history_summary}"
        if req.auto_retrieve:
            system += "\n\n注意：知识库中未检索到相关资料。如实告知用户，建议上传相关论文。"

    llm_messages: list[dict]
    if history_summary:
        llm_messages = messages_for_llm[-4:]
    else:
        llm_messages = messages_for_llm[-8:]
    llm_messages.append({"role": "user", "content": req.query})

    try:
        answer = await adapter.chat(
            model=model_name,
            messages=llm_messages,
            system=system,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        )
    except Exception as e:
        logger.error(f"LLM call failed: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"LLM 调用失败：{str(e)}")

    _save_to_history(
        session_id,
        req.query,
        answer,
        messages_full,
        sources=sources,
        rewritten_query=rewritten_display if rewritten_display and rewritten_display != req.query else None,
    )

    note_id = None
    if req.auto_save_note:
        note_id = await _auto_save_note(messages_for_llm, req.query, answer, model)

    return ProxyChatResponse(
        answer=answer,
        model_used=model,
        sources=sources,
        note_id=note_id,
        session_id=session_id,
        intent=intent,
        rewritten_query=rewritten_display if rewritten_display and rewritten_display != req.query else None,
    )


@router.post("/chat/stream")
async def proxy_chat_stream(req: ProxyChatRequest):
    from backend.config import get_settings

    settings = get_settings()
    model = req.model or settings.default_llm_model
    session_id = req.session_id or str(uuid.uuid4())

    try:
        adapter, model_name = resolve_adapter(model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    intent = classify_intent(req.query)

    async def event_generator():
        nonlocal session_id

        if intent == "intro":
            answer = f"""你好！{SELF_INTRO}

使用方式：
- 直接提问学术问题，我会自动检索知识库里的论文
- 讨论结束后，在侧边栏点“保存为笔记”，我会自动提炼要点
- 下次提问时，之前保存的笔记也会被检索到

试试这些问题：
- 帮我分析这篇论文的创新点
- XXX 方法和 YYY 方法有什么区别？
- 总结一下知识库里关于 ZZZ 的研究进展"""
            yield _sse_event({
                "type": "meta",
                "sources": [],
                "intent": "intro",
                "session_id": session_id,
                "rewritten_query": None,
            })
            yield _sse_event({"type": "token", "content": answer})
            yield _sse_event({"type": "done"})
            _save_to_history(session_id, req.query, answer, [])
            return

        if intent == "chat":
            try:
                answer = await adapter.chat(
                    model=model_name,
                    messages=[{"role": "user", "content": req.query}],
                    system=CHAT_SYSTEM,
                    temperature=0.8,
                    max_tokens=500,
                )
            except Exception:
                answer = "你好！我是 ScholarAgent，有什么学术问题我可以帮你的吗？"

            yield _sse_event({
                "type": "meta",
                "sources": [],
                "intent": "chat",
                "session_id": session_id,
                "rewritten_query": None,
            })
            yield _sse_event({"type": "token", "content": answer})
            yield _sse_event({"type": "done"})
            _save_to_history(session_id, req.query, answer, [])
            return

        if intent == "unsupported":
            yield _sse_event({
                "type": "meta",
                "sources": [],
                "intent": "unsupported",
                "session_id": session_id,
                "rewritten_query": None,
            })
            yield _sse_event({"type": "token", "content": UNSUPPORTED_RESPONSE})
            yield _sse_event({"type": "done"})
            _save_to_history(session_id, req.query, UNSUPPORTED_RESPONSE, [])
            return

        messages_full: list[dict] = []
        if req.messages:
            messages_full = [{"role": m.role, "content": m.content} for m in req.messages]
        elif req.session_id:
            messages_full = get_chat_history().load_session(req.session_id)

        messages_for_llm = [
            {
                "role": message.get("role", "user"),
                "content": message.get("content", ""),
            }
            for message in messages_full
            if isinstance(message, dict) and message.get("content")
        ]

        rewritten: dict = {"en_queries": [req.query], "zh_query": req.query}
        paper_results: list[dict] = []
        note_results: list[dict] = []

        if req.auto_retrieve:
            rewrite_task = asyncio.create_task(
                rewrite_query_with_timeout(req.query, adapter, model_name, timeout_seconds=3.0)
            )
            base_retrieve_task = asyncio.create_task(
                concurrent_retrieve(
                    queries=[req.query],
                    user_id=MOCK_USER_ID,
                    retrieve_papers=req.retrieve_papers,
                    retrieve_notes=req.retrieve_notes,
                    top_k=req.top_k,
                    doc_type=req.doc_type,
                )
            )

            rewritten = await rewrite_task
            base_papers, base_notes = await base_retrieve_task
            paper_results = list(base_papers)
            note_results = list(base_notes)

            extra_queries = [
                query
                for query in rewritten.get("en_queries", [])
                if query and query != req.query
            ]

            if extra_queries:
                extra_papers, extra_notes = await concurrent_retrieve(
                    queries=extra_queries,
                    user_id=MOCK_USER_ID,
                    retrieve_papers=req.retrieve_papers,
                    retrieve_notes=req.retrieve_notes,
                    top_k=req.top_k,
                    doc_type=req.doc_type,
                )

                seen_chunk_ids = {
                    item.get("chunk_id")
                    for item in paper_results
                    if item.get("chunk_id")
                }
                for item in extra_papers:
                    chunk_id = item.get("chunk_id")
                    if chunk_id and chunk_id in seen_chunk_ids:
                        continue
                    if chunk_id:
                        seen_chunk_ids.add(chunk_id)
                    paper_results.append(item)

                seen_note_keys = {
                    (item.get("note_id"), item.get("title"))
                    for item in note_results
                }
                for item in extra_notes:
                    key = (item.get("note_id"), item.get("title"))
                    if key in seen_note_keys:
                        continue
                    seen_note_keys.add(key)
                    note_results.append(item)

            paper_results.sort(key=lambda x: x.get("score", 0), reverse=True)
            note_results.sort(key=lambda x: x.get("score", 0), reverse=True)
            paper_results = paper_results[: req.top_k]
            note_results = note_results[:3]
        else:
            rewritten = await rewrite_query_with_timeout(req.query, adapter, model_name, timeout_seconds=3.0)

        rewritten_display = " / ".join(rewritten.get("en_queries", []))

        context_parts: list[str] = []
        sources: list[SourceItem] = []
        ref_index = 1

        for result in paper_results:
            content = result.get("content", "")
            section = result.get("section_title", "")
            score = result.get("score", 0)
            if score < 0.25:
                continue

            label = f"[{ref_index}]"
            if section:
                label += f" ({section})"
            context_parts.append(f"{label}\n{content[:600]}")

            sources.append(
                SourceItem(
                    type="paper",
                    chunk_id=result.get("chunk_id"),
                    document_id=result.get("document_id"),
                    section_title=section,
                    content=content[:300],
                    score=score,
                )
            )
            ref_index += 1

        for note in note_results:
            summary = note.get("summary", "")
            title = note.get("title", "")
            score = note.get("score", 0)
            if score < 0.25 or not summary:
                continue

            label = f"[{ref_index}]"
            if title:
                label += f" (笔记：{title})"
            context_parts.append(f"{label}\n{summary[:400]}")

            sources.append(
                SourceItem(
                    type="note",
                    note_id=note.get("note_id"),
                    title=title,
                    content=summary[:300],
                    score=score,
                )
            )
            ref_index += 1

        if context_parts:
            system = RAG_SYSTEM_WITH_CONTEXT.format(
                context="\n\n".join(context_parts),
                history_summary="",
            )
        else:
            system = RAG_SYSTEM
            if req.auto_retrieve:
                system += "\n\n注意：知识库中未检索到相关资料。如实告知用户，建议上传相关论文。"

        llm_messages = messages_for_llm[-8:]
        llm_messages.append({"role": "user", "content": req.query})

        yield _sse_event(
            {
                "type": "meta",
                "sources": [source.model_dump() for source in sources],
                "intent": intent,
                "session_id": session_id,
                "rewritten_query": rewritten_display if rewritten_display and rewritten_display != req.query else None,
            }
        )

        chunks: list[str] = []
        try:
            async for token in adapter.chat_stream(
                model=model_name,
                messages=llm_messages,
                system=system,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
            ):
                if not token:
                    continue
                chunks.append(token)
                yield _sse_event({"type": "token", "content": token})
        except Exception as e:
            error_text = f"LLM 调用失败：{str(e)}"
            logger.error(error_text, exc_info=True)
            chunks.append(error_text)
            yield _sse_event({"type": "token", "content": error_text})

        answer = "".join(chunks)
        yield _sse_event({"type": "done"})

        _save_to_history(
            session_id,
            req.query,
            answer,
            messages_full,
            sources=sources,
            rewritten_query=rewritten_display if rewritten_display and rewritten_display != req.query else None,
        )

        if req.auto_save_note:
            await _auto_save_note(messages_for_llm, req.query, answer, model)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ========================
# Session & Model APIs
# ========================

@router.get("/sessions")
async def list_sessions():
    return {"sessions": get_chat_history().list_sessions(MOCK_USER_ID)}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    messages = get_chat_history().load_session(session_id)
    return {"session_id": session_id, "messages": messages}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    return {"deleted": get_chat_history().delete_session(session_id)}


@router.get("/models")
async def list_models():
    return {"models": list_available_models()}
