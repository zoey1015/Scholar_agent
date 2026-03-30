"""
ScholarAgent CLI 工具

底层调用代理编排 API，实现一行命令完成带知识库上下文的对话。

Usage:
    scholar chat "transformer attention 优化方法"
    scholar chat --model deepseek-chat "对比 FlashAttention 和 Linear Attention"
    scholar search "多模态学习"
    scholar upload paper.pdf
    scholar tasks --status pending
"""

import typer
import httpx
from rich.console import Console
from rich.markdown import Markdown

app = typer.Typer(name="scholar", help="ScholarAgent CLI - 科研知识库命令行工具")
console = Console()

API_BASE = "http://localhost:8000/api/v1"


@app.command()
def chat(
    query: str = typer.Argument(..., help="问题或讨论内容"),
    model: str = typer.Option("claude-sonnet-4-20250514", "--model", "-m", help="目标模型"),
    no_retrieve: bool = typer.Option(False, "--no-retrieve", help="不检索知识库"),
    save_note: bool = typer.Option(False, "--save-note", help="自动保存对话总结"),
):
    """带知识库上下文的 AI 对话（调用代理编排 API）"""
    console.print(f"[bold blue]Model:[/] {model}")
    console.print(f"[bold blue]Query:[/] {query}\n")

    with console.status("Searching knowledge base & calling model..."):
        try:
            resp = httpx.post(
                f"{API_BASE}/proxy/chat",
                json={
                    "query": query,
                    "model": model,
                    "auto_retrieve": not no_retrieve,
                    "auto_save_note": save_note,
                },
                timeout=120.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            console.print(f"[red]API Error:[/] {e}")
            raise typer.Exit(1)

    # 显示引用来源
    sources = data.get("sources", [])
    if sources:
        console.print(f"[dim]Found {len(sources)} relevant sources from knowledge base.[/dim]\n")

    # 显示回答
    console.print(Markdown(data["answer"]))


@app.command()
def search(
    query: str = typer.Argument(..., help="检索关键词"),
    top_k: int = typer.Option(5, "--top-k", "-k"),
    doc_type: str = typer.Option("all", "--type", "-t", help="all / paper / patent"),
):
    """搜索知识库"""
    with console.status("Searching..."):
        try:
            resp = httpx.post(
                f"{API_BASE}/documents/search",
                json={"query": query, "top_k": top_k, "doc_type": doc_type},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            console.print(f"[red]API Error:[/] {e}")
            raise typer.Exit(1)

    results = data.get("results", [])
    if not results:
        console.print("[yellow]No results found.[/yellow]")
        return

    for i, r in enumerate(results):
        console.print(f"\n[bold][{i+1}][/bold] {r.get('section_title', 'N/A')}")
        console.print(f"  [dim]{r.get('content', '')[:200]}...[/dim]")


@app.command()
def upload(
    file: str = typer.Argument(..., help="PDF 文件路径"),
):
    """上传论文/专利 PDF"""
    import os
    if not os.path.exists(file):
        console.print(f"[red]File not found:[/] {file}")
        raise typer.Exit(1)

    with console.status("Uploading..."):
        try:
            with open(file, "rb") as f:
                resp = httpx.post(
                    f"{API_BASE}/documents/upload",
                    files={"file": (os.path.basename(file), f, "application/pdf")},
                    timeout=60.0,
                )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            console.print(f"[red]Upload Error:[/] {e}")
            raise typer.Exit(1)

    console.print(f"[green]Uploaded![/green] Document ID: {data['document_id']}")
    console.print(f"Task ID: {data['task_id']} (status: {data['status']})")


@app.command()
def tasks(
    status: str = typer.Option("all", "--status", "-s", help="Filter by status"),
):
    """查看异步任务状态"""
    # TODO Phase 2: 调用 /api/v1/tasks
    console.print("[yellow]Task status query will be available in Phase 2.[/yellow]")


if __name__ == "__main__":
    app()
