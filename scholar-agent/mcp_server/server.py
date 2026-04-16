"""
ScholarAgent MCP Server（Phase 2 完整版）

Tools:
  - search_papers     语义检索论文/专利
  - get_paper_detail  获取文档详情
  - save_note         保存对话为结构化研究笔记
  - search_notes      检索历史研究笔记
  - get_task_status   查询异步任务状态
"""

import asyncio
import logging
import sys

sys.path.insert(0, ".")

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from backend.skills.base import SkillContext
from backend.skills.registry import init_skills, skill_registry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MOCK_USER_ID = "00000000-0000-0000-0000-000000000001"

app = Server("scholar-agent")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_papers",
            description="从知识库中语义检索论文和专利。支持中英文查询，返回相关文档片段。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词或自然语言描述"},
                    "doc_type": {
                        "type": "string",
                        "enum": ["all", "paper", "patent"],
                        "default": "all",
                    },
                    "top_k": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_paper_detail",
            description="获取指定文档的详细信息，包括摘要、作者、章节等。",
            inputSchema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "文档 ID"},
                },
                "required": ["document_id"],
            },
        ),
        Tool(
            name="save_note",
            description=(
                "将当前对话的内容保存为结构化研究笔记。"
                "会自动提取：核心问题、创新点、假设、结论、待验证实验。"
                "建议在每次有价值的讨论结束后调用。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "conversation": {
                        "type": "string",
                        "description": "需要保存的对话内容（复制对话文本）",
                    },
                    "title": {
                        "type": "string",
                        "description": "笔记标题（可选，不填则自动生成）",
                    },
                    "cited_doc_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "对话中涉及的文档 ID 列表（可选）",
                    },
                },
                "required": ["conversation"],
            },
        ),
        Tool(
            name="search_notes",
            description="检索历史研究笔记。可以找到之前讨论过的创新点、结论、待验证假设。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词"},
                    "top_k": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_task_status",
            description="查询异步任务状态（论文解析、向量化等）",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                },
                "required": ["task_id"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "search_papers":
        return await _handle_search_papers(arguments)
    elif name == "get_paper_detail":
        return await _handle_get_paper_detail(arguments)
    elif name == "save_note":
        return await _handle_save_note(arguments)
    elif name == "search_notes":
        return await _handle_search_notes(arguments)
    elif name == "get_task_status":
        return await _handle_get_task_status(arguments)
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ========================
# Tool Handlers
# ========================

async def _handle_search_papers(args: dict) -> list[TextContent]:
    """search_papers: RetrievalSkill → 格式化输出"""
    skill = skill_registry.get("retrieval")
    if not skill:
        return [TextContent(type="text", text="检索服务暂不可用。")]

    context = SkillContext(
        user_id=MOCK_USER_ID,
        query=args["query"],
        metadata={
            "top_k": args.get("top_k", 5),
            "doc_type": args.get("doc_type", "all"),
        },
    )

    result = await skill.execute(context)
    results = result.data.get("results", []) if result.data else []

    if not results:
        return [TextContent(
            type="text",
            text="知识库中未找到相关内容。请确认已上传相关论文，或尝试换一个关键词。"
        )]

    lines = [f"找到 {len(results)} 条相关内容：\n"]
    for i, r in enumerate(results):
        score = r.get("score", 0)
        lines.append(
            f"[{i+1}] 章节：{r.get('section_title', 'N/A')}\n"
            f"    相关度：{score:.3f}\n"
            f"    内容：{r.get('content', '')[:300]}...\n"
            f"    文档ID：{r.get('document_id', '')}\n"
        )

    return [TextContent(type="text", text="\n".join(lines))]


async def _handle_get_paper_detail(args: dict) -> list[TextContent]:
    """get_paper_detail: 查询 PostgreSQL → 返回文档详情"""
    from backend.db.postgres import SyncSession
    from backend.models.document import Document
    from sqlalchemy import select
    import uuid

    doc_id = args.get("document_id", "")
    if not doc_id:
        return [TextContent(type="text", text="请提供 document_id。")]

    db = SyncSession()
    try:
        try:
            stmt = select(Document).where(Document.id == uuid.UUID(doc_id))
            doc = db.execute(stmt).scalar_one_or_none()
        except ValueError:
            return [TextContent(type="text", text=f"无效的 document_id: {doc_id}")]

        if not doc:
            return [TextContent(type="text", text=f"未找到文档: {doc_id}")]

        authors = doc.authors or []
        author_names = [a.get("name", "") for a in authors if isinstance(a, dict)]

        lines = [
            f"📄 {doc.title}",
            f"作者：{', '.join(author_names) if author_names else '未知'}",
            f"类型：{doc.doc_type} | 语言：{doc.language}",
            f"解析状态：{doc.parse_status}",
        ]

        if doc.abstract:
            lines.append(f"\n摘要：\n{doc.abstract[:800]}")
            if len(doc.abstract) > 800:
                lines.append("...[摘要已截断]")

        if doc.tags:
            lines.append(f"\n关键词：{', '.join(doc.tags)}")

        return [TextContent(type="text", text="\n".join(lines))]
    finally:
        db.close()


async def _handle_save_note(args: dict) -> list[TextContent]:
    """
    save_note: ConversationSummarySkill → NotesService.save_note

    MCP Tool 是异步的，但 NotesService 内部用同步 DB。
    在 executor 里运行以避免 event loop 冲突。
    """
    conversation = args.get("conversation", "").strip()
    if not conversation:
        return [TextContent(type="text", text="请提供 conversation 内容。")]

    if len(conversation) < 50:
        return [TextContent(type="text", text="对话内容太短，无法提炼笔记（至少 50 字）。")]

    # Step 1: 调用 ConversationSummarySkill
    skill = skill_registry.get("conversation_summary")
    if not skill:
        return [TextContent(type="text", text="对话总结服务暂不可用。")]

    context = SkillContext(
        user_id=MOCK_USER_ID,
        metadata={
            "conversation": conversation,
            "title": args.get("title", ""),
            "source_platform": "claude",
            "cited_doc_ids": args.get("cited_doc_ids", []),
        },
    )

    result = await skill.execute(context)

    if result.status.value == "failed":
        return [TextContent(type="text", text=f"笔记保存失败：{result.message}")]

    # Step 2: 保存到 DB + Milvus（在 executor 中运行同步代码）
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _save():
        from backend.services.notes_service import get_notes_service
        return get_notes_service().save_note(
            user_id=MOCK_USER_ID,
            note_data=result.data,
            source_type="mcp",
        )

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        note_id = await loop.run_in_executor(pool, _save)

    # 格式化输出
    data = result.data
    lines = [
        f"✅ 笔记已保存！",
        f"📝 标题：{data.get('title', '未命名')}",
        f"🆔 笔记ID：{note_id}",
        f"\n📋 内容概述：{data.get('summary', '')[:200]}",
    ]

    if data.get("innovations"):
        lines.append(f"\n💡 创新点：")
        for item in data["innovations"][:3]:
            lines.append(f"  • {item}")

    if data.get("conclusions"):
        lines.append(f"\n🎯 主要结论：")
        for item in data["conclusions"][:3]:
            lines.append(f"  • {item}")

    if data.get("experiments_todo"):
        lines.append(f"\n🔬 待验证实验：")
        for item in data["experiments_todo"][:3]:
            lines.append(f"  • {item}")

    return [TextContent(type="text", text="\n".join(lines))]


async def _handle_search_notes(args: dict) -> list[TextContent]:
    """search_notes: NotesService.search_notes"""
    query = args.get("query", "").strip()
    if not query:
        return [TextContent(type="text", text="请提供检索关键词。")]

    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _search():
        from backend.services.notes_service import get_notes_service
        return get_notes_service().search_notes(
            user_id=MOCK_USER_ID,
            query=query,
            top_k=args.get("top_k", 5),
        )

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        results = await loop.run_in_executor(pool, _search)

    if not results:
        return [TextContent(
            type="text",
            text=f"未找到与「{query}」相关的历史笔记。"
        )]

    lines = [f"找到 {len(results)} 条相关笔记：\n"]
    for i, r in enumerate(results):
        lines.append(
            f"[{i+1}] 📝 {r.get('title', '未命名')}\n"
            f"    {r.get('summary', '')[:200]}\n"
            f"    笔记ID：{r.get('note_id', '')}\n"
        )

    return [TextContent(type="text", text="\n".join(lines))]


async def _handle_get_task_status(args: dict) -> list[TextContent]:
    """get_task_status: 查询 async_tasks 表"""
    task_id = args.get("task_id", "")
    if not task_id:
        return [TextContent(type="text", text="请提供 task_id。")]

    from backend.db.postgres import SyncSession
    from backend.models.document import AsyncTask
    from sqlalchemy import select
    import uuid

    db = SyncSession()
    try:
        stmt = select(AsyncTask).where(AsyncTask.id == uuid.UUID(task_id))
        task = db.execute(stmt).scalar_one_or_none()
        if not task:
            return [TextContent(type="text", text=f"未找到任务: {task_id}")]

        status_emoji = {
            "pending": "⏳",
            "processing": "🔄",
            "success": "✅",
            "failed": "❌",
        }.get(task.status, "❓")

        lines = [
            f"{status_emoji} 任务状态：{task.status}",
            f"任务类型：{task.task_type}",
            f"创建时间：{task.created_at.strftime('%Y-%m-%d %H:%M:%S') if task.created_at else 'N/A'}",
        ]

        if task.result_data:
            lines.append(f"结果：{task.result_data}")
        if task.error_message:
            lines.append(f"错误：{task.error_message[:200]}")

        return [TextContent(type="text", text="\n".join(lines))]
    finally:
        db.close()


# ========================
# 入口
# ========================

async def main():
    logger.info("Starting ScholarAgent MCP Server (Phase 2)...")
    init_skills()
    logger.info(f"Loaded {skill_registry.count} skills.")

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
