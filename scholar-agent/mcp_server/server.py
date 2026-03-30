"""
ScholarAgent MCP Server

将系统核心能力暴露为 MCP Tools，供 Claude 桌面端等支持 MCP 的客户端调用。
MCP Tool 是面向外部客户端的粗粒度接口，内部编排多个 Skill。

启动方式:
    python -m mcp_server.server

Claude Desktop 配置 (claude_desktop_config.json):
{
    "mcpServers": {
        "scholar-agent": {
            "command": "python",
            "args": ["-m", "mcp_server.server"],
            "cwd": "/path/to/scholar-agent"
        }
    }
}
"""

import asyncio
import logging
import sys

# 确保项目根目录在 Python path 中
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
    """注册 MCP Tools"""
    return [
        Tool(
            name="search_papers",
            description="从知识库中语义检索论文和专利。支持中英文查询。返回相关文档片段。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词或自然语言描述"},
                    "doc_type": {
                        "type": "string",
                        "enum": ["all", "paper", "patent"],
                        "default": "all",
                        "description": "筛选文档类型",
                    },
                    "top_k": {"type": "integer", "default": 5, "description": "返回数量"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_paper_detail",
            description="获取指定文档的详细内容，包括摘要、章节全文等。",
            inputSchema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "文档 ID"},
                    "section": {
                        "type": "string",
                        "description": "指定章节（可选），如 'abstract', 'method', 'results'",
                    },
                },
                "required": ["document_id"],
            },
        ),
        Tool(
            name="save_note",
            description="将当前对话的讨论总结保存为研究笔记，存入知识库。会自动提取创新点、关键问题、待验证假设。",
            inputSchema={
                "type": "object",
                "properties": {
                    "conversation": {"type": "string", "description": "需要总结的对话内容"},
                    "title": {"type": "string", "description": "笔记标题（可选，不填则自动生成）"},
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
                    "task_id": {"type": "string", "description": "任务 ID"},
                },
                "required": ["task_id"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """
    Tool 调用入口

    MCP Tool → 编排 Skill → 返回结果
    """

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
        return [TextContent(type="text", text="RetrievalSkill is not available.")]

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
        return [TextContent(type="text", text="知识库中未找到相关内容。请确认已上传相关论文。")]

    # 格式化检索结果
    formatted = []
    for i, r in enumerate(results):
        formatted.append(
            f"[{i+1}] {r.get('section_title', 'N/A')}\n"
            f"    来源: {r.get('document_title', 'Unknown')}\n"
            f"    内容: {r.get('content', '')[:300]}...\n"
        )

    return [TextContent(type="text", text="\n".join(formatted))]


async def _handle_get_paper_detail(args: dict) -> list[TextContent]:
    """get_paper_detail: 查询 PostgreSQL → 返回文档详情"""
    # TODO Phase 1: 查询数据库
    return [TextContent(type="text", text=f"Document {args['document_id']}: detail not implemented yet.")]


async def _handle_save_note(args: dict) -> list[TextContent]:
    """save_note: ConversationSummarySkill → EmbeddingSkill"""
    # TODO Phase 2: 实现对话总结 + 向量化存储
    return [TextContent(type="text", text="笔记保存功能将在 Phase 2 实现。")]


async def _handle_search_notes(args: dict) -> list[TextContent]:
    """search_notes: RetrievalSkill（notes collection）"""
    # TODO Phase 2: 检索笔记集合
    return [TextContent(type="text", text="笔记检索功能将在 Phase 2 实现。")]


async def _handle_get_task_status(args: dict) -> list[TextContent]:
    """get_task_status: 查询 async_tasks 表"""
    # TODO Phase 2: 查询任务状态
    return [TextContent(type="text", text=f"Task {args['task_id']}: status query not implemented yet.")]


# ========================
# 入口
# ========================

async def main():
    logger.info("Starting ScholarAgent MCP Server...")
    init_skills()
    logger.info(f"Loaded {skill_registry.count} skills.")

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
