"""
ScholarAgent CLI（完整实现）

Usage:
    scholar chat "transformer attention 优化方法"
    scholar chat --model qwen-plus "对比 FlashAttention 和 Linear Attention"
    scholar chat --model qwen-max --save-note "复杂问题，自动保存笔记"
    scholar search "多模态学习"
    scholar notes search "Lyapunov稳定性"
    scholar notes list
    scholar upload paper.pdf
    scholar tasks <task_id>
"""

import typer
import httpx
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich import box

app = typer.Typer(
    name="scholar",
    help="ScholarAgent CLI - 科研知识库命令行工具",
    no_args_is_help=True,
)
notes_app = typer.Typer(help="研究笔记管理")
app.add_typer(notes_app, name="notes")

console = Console()
API_BASE = "http://localhost:8000/api/v1"


# ========================
# chat 命令
# ========================

@app.command()
def chat(
    query: str = typer.Argument(..., help="问题或讨论内容"),
    model: str = typer.Option("qwen-plus", "--model", "-m", help="目标模型"),
    no_retrieve: bool = typer.Option(False, "--no-retrieve", help="不检索知识库"),
    no_notes: bool = typer.Option(False, "--no-notes", help="不检索历史笔记"),
    save_note: bool = typer.Option(False, "--save-note", "-s", help="自动保存对话为笔记"),
    top_k: int = typer.Option(5, "--top-k", "-k", help="检索数量"),
):
    """带知识库上下文的 AI 对话"""
    console.print(f"\n[bold cyan]Model:[/] {model}")
    console.print(f"[bold cyan]Query:[/] {query}\n")

    with console.status("[dim]Searching knowledge base & calling model...[/dim]"):
        try:
            resp = httpx.post(
                f"{API_BASE}/proxy/chat",
                json={
                    "query": query,
                    "model": model,
                    "auto_retrieve": not no_retrieve,
                    "retrieve_papers": not no_retrieve,
                    "retrieve_notes": not no_notes,
                    "top_k": top_k,
                    "auto_save_note": save_note,
                },
                timeout=120.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            console.print(f"[red]API Error {e.response.status_code}:[/] {e.response.text}")
            raise typer.Exit(1)
        except httpx.TimeoutException:
            console.print("[red]Request timed out. The model may be slow to respond.[/]")
            raise typer.Exit(1)
        except httpx.ConnectError:
            console.print(f"[red]Cannot connect to API at {API_BASE}[/]")
            console.print("[dim]Make sure the backend is running: sudo docker-compose up -d[/dim]")
            raise typer.Exit(1)

    # 显示引用来源
    sources = data.get("sources", [])
    if sources:
        console.print(f"[dim]📚 Referenced {len(sources)} sources from knowledge base[/dim]\n")
        for i, s in enumerate(sources[:3]):
            src_type = "📄" if s.get("type") == "paper" else "📝"
            title = s.get("section_title") or s.get("title") or "N/A"
            score = s.get("score", 0)
            console.print(f"[dim]  {src_type} [{i+1}] {title} (relevance: {score:.2f})[/dim]")
        console.print()

    # 显示回答
    console.print(Panel(
        Markdown(data["answer"]),
        title=f"[bold green]{model}[/]",
        border_style="green",
    ))

    # 显示笔记保存信息
    if data.get("note_id"):
        console.print(f"\n[dim]✅ Note auto-saved: {data['note_id']}[/dim]")


# ========================
# search 命令（论文检索）
# ========================

@app.command()
def search(
    query: str = typer.Argument(..., help="检索关键词"),
    top_k: int = typer.Option(5, "--top-k", "-k"),
    doc_type: str = typer.Option("all", "--type", "-t", help="all / paper / patent"),
):
    """搜索论文知识库"""
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

    console.print(f"\n[bold]Found {len(results)} results for:[/] {query}\n")
    for i, r in enumerate(results):
        score = r.get("score", 0)
        section = r.get("section_title", "N/A")
        content = r.get("content", "")[:200]
        console.print(f"[bold cyan][{i+1}][/] {section} [dim](score: {score:.3f})[/dim]")
        console.print(f"  [dim]{content}...[/dim]\n")


# ========================
# upload 命令
# ========================

@app.command()
def upload(
    file: str = typer.Argument(..., help="PDF 文件路径"),
    doc_type: str = typer.Option("paper", "--type", "-t", help="paper / patent"),
    language: str = typer.Option("en", "--lang", "-l", help="en / zh / mixed"),
):
    """上传论文/专利 PDF"""
    import os
    if not os.path.exists(file):
        console.print(f"[red]File not found:[/] {file}")
        raise typer.Exit(1)

    file_size = os.path.getsize(file) / 1024 / 1024
    console.print(f"Uploading [cyan]{os.path.basename(file)}[/] ({file_size:.1f} MB)...")

    with console.status("Uploading..."):
        try:
            with open(file, "rb") as f:
                resp = httpx.post(
                    f"{API_BASE}/documents/upload",
                    files={"file": (os.path.basename(file), f, "application/pdf")},
                    params={"doc_type": doc_type, "language": language},
                    timeout=60.0,
                )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            console.print(f"[red]Upload Error:[/] {e}")
            raise typer.Exit(1)

    console.print(f"[green]✅ Uploaded![/green]")
    console.print(f"  Document ID: [cyan]{data['document_id']}[/]")
    console.print(f"  Task ID:     [cyan]{data['task_id']}[/]")
    console.print(f"\n[dim]Use 'scholar tasks {data['task_id']}' to check parsing status[/dim]")


# ========================
# tasks 命令
# ========================

@app.command()
def tasks(
    task_id: str = typer.Argument(..., help="任务 ID"),
):
    """查看异步任务状态"""
    try:
        resp = httpx.get(f"{API_BASE}/documents/tasks/{task_id}", timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        console.print(f"[red]API Error:[/] {e}")
        raise typer.Exit(1)

    status = data.get("status", "unknown")
    status_color = {
        "pending": "yellow",
        "processing": "cyan",
        "success": "green",
        "failed": "red",
    }.get(status, "white")

    console.print(f"\nTask: [cyan]{task_id}[/]")
    console.print(f"Type: {data.get('task_type', 'N/A')}")
    console.print(f"Status: [{status_color}]{status}[/{status_color}]")

    if data.get("result_data"):
        console.print(f"Result: {data['result_data']}")
    if data.get("error_message"):
        console.print(f"[red]Error: {data['error_message']}[/]")


# ========================
# notes 子命令
# ========================

@notes_app.command("save")
def notes_save(
    conversation: str = typer.Argument(..., help="对话内容"),
    title: str = typer.Option("", "--title", "-t", help="笔记标题"),
):
    """保存对话为结构化研究笔记"""
    with console.status("Summarizing and saving..."):
        try:
            resp = httpx.post(
                f"{API_BASE}/notes/save",
                json={
                    "conversation": conversation,
                    "title": title,
                    "source_platform": "cli",
                },
                timeout=60.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            console.print(f"[red]API Error:[/] {e}")
            raise typer.Exit(1)

    console.print(f"[green]✅ Note saved![/green]")
    console.print(f"  Note ID: [cyan]{data['note_id']}[/]")
    console.print(f"  Title:   {data['title']}")


@notes_app.command("search")
def notes_search(
    query: str = typer.Argument(..., help="检索关键词"),
    top_k: int = typer.Option(5, "--top-k", "-k"),
):
    """检索历史研究笔记"""
    with console.status("Searching notes..."):
        try:
            resp = httpx.post(
                f"{API_BASE}/notes/search",
                json={"query": query, "top_k": top_k},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            console.print(f"[red]API Error:[/] {e}")
            raise typer.Exit(1)

    results = data.get("results", [])
    if not results:
        console.print(f"[yellow]No notes found for:[/] {query}")
        return

    console.print(f"\n[bold]Found {len(results)} notes for:[/] {query}\n")
    for i, r in enumerate(results):
        score = r.get("score", 0)
        console.print(f"[bold cyan][{i+1}][/] {r.get('title', 'Untitled')} [dim](score: {score:.3f})[/dim]")
        console.print(f"  [dim]{r.get('summary', '')[:150]}...[/dim]")
        console.print(f"  [dim]ID: {r.get('note_id', '')}[/dim]\n")


@notes_app.command("list")
def notes_list(
    page: int = typer.Option(1, "--page", "-p"),
    page_size: int = typer.Option(10, "--size"),
):
    """列出所有研究笔记"""
    try:
        resp = httpx.get(
            f"{API_BASE}/notes",
            params={"page": page, "page_size": page_size},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        console.print(f"[red]API Error:[/] {e}")
        raise typer.Exit(1)

    notes = data.get("notes", [])
    total = data.get("total", 0)

    if not notes:
        console.print("[yellow]No notes yet. Use 'scholar notes save' to create one.[/yellow]")
        return

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan")
    table.add_column("Title", style="white", max_width=40)
    table.add_column("Summary", style="dim", max_width=50)
    table.add_column("Created", style="dim", max_width=20)

    for n in notes:
        created = n.get("created_at", "")[:10] if n.get("created_at") else "N/A"
        summary = (n.get("summary") or "")[:80]
        table.add_row(n.get("title", "Untitled"), summary, created)

    console.print(f"\n[bold]Research Notes[/] (total: {total})\n")
    console.print(table)


@notes_app.command("show")
def notes_show(note_id: str = typer.Argument(..., help="笔记 ID")):
    """显示笔记详情"""
    try:
        resp = httpx.get(f"{API_BASE}/notes/{note_id}", timeout=10.0)
        resp.raise_for_status()
        n = resp.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            console.print(f"[red]Note not found:[/] {note_id}")
        else:
            console.print(f"[red]API Error:[/] {e}")
        raise typer.Exit(1)

    console.print(Panel(
        f"[bold]{n.get('title', 'Untitled')}[/bold]\n\n"
        f"[dim]Created: {(n.get('created_at') or '')[:19]}[/dim]",
        border_style="cyan",
    ))

    if n.get("summary"):
        console.print(f"\n[bold]📋 Summary[/]\n{n['summary']}\n")

    sections = [
        ("💡 Innovations", "innovations"),
        ("❓ Key Questions", "key_questions"),
        ("🔬 Hypotheses", "hypotheses"),
        ("🎯 Conclusions", "conclusions"),
        ("🧪 Experiments Todo", "experiments_todo"),
    ]

    for label, key in sections:
        items = n.get(key, [])
        if items:
            console.print(f"[bold]{label}[/]")
            for item in items:
                console.print(f"  • {item}")
            console.print()


# ========================
# models 命令
# ========================

@app.command()
def models():
    """列出支持的 LLM 模型"""
    try:
        resp = httpx.get(f"{API_BASE}/proxy/models", timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        console.print(f"[red]API Error:[/] {e}")
        raise typer.Exit(1)

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan")
    table.add_column("Model", style="cyan")
    table.add_column("Provider", style="dim")
    table.add_column("Description", style="white")

    for m in data.get("models", []):
        table.add_row(m["model"], m["provider"], m["desc"])

    console.print("\n[bold]Available Models[/]\n")
    console.print(table)
    console.print(
        "\n[dim]Usage: scholar chat --model <model_name> \"your question\"[/dim]\n"
    )


if __name__ == "__main__":
    app()
