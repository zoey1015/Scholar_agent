<div align="center">

# ScholarAgent

### Open-Source Research Knowledge Backend

<p>
    <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white" alt="python">
    <img src="https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white" alt="fastapi">
    <img src="https://img.shields.io/badge/LangGraph-Agent%20Workflow-1f6feb" alt="langgraph">
    <img src="https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white" alt="docker">
    <img src="https://img.shields.io/badge/Streamlit-1.41-FF4B4B?logo=streamlit&logoColor=white" alt="streamlit">
    <img src="https://img.shields.io/badge/Status-Active-success" alt="status">
</p>

</div>

ScholarAgent 是一个面向科研场景的知识后端。  
它把论文解析、语义检索、研究笔记、深度研究流程和多模型对话统一在同一套架构中，支持 Web、CLI、API、MCP 多入口访问。

## 目录

- [项目亮点](#项目亮点)
- [系统架构](#系统架构)
- [快速复现](#快速复现)
- [使用方式](#使用方式)
- [本地开发](#本地开发)
- [常见问题](#常见问题)
- [项目结构](#项目结构)
- [路线图](#路线图)
- [贡献与开源](#贡献与开源)

## 项目亮点

| 能力模块 | 具体能力 | 解决问题 |
| --- | --- | --- |
| 文档入库 | PDF 上传后自动异步解析，抽取标题、摘要、章节、引用并分块 | 让论文内容可结构化管理与检索 |
| 语义检索 | 基于向量检索召回论文/专利内容，支持中英文查询 | 减少手工翻文献时间 |
| 研究笔记 | 对话自动提炼为结构化笔记，并可二次检索 | 防止研究结论散落在聊天记录里 |
| 代理对话 | Query Rewrite、并发检索、会话记忆、流式输出 | 在问答中自动融合知识库上下文 |
| 深度研究 | Plan-Execute-Replan 流程 + 关系分析 + 状态追踪 | 支持复杂调研和跨论文比较 |
| 多入口集成 | REST API + Streamlit + CLI + MCP | 适配不同研究与开发工作流 |

## 系统架构

```mermaid
flowchart TB
        subgraph Access[接入层]
            A1[Web Frontend]
            A2[CLI]
            A3[MCP Server]
            A4[REST API]
        end

        subgraph Orchestration[编排层]
            B1[FastAPI]
            B2[LangGraph]
            B3[Skills Registry]
            B4[Celery Tasks]
        end

        subgraph Capability[能力层]
            C1[Paper Parsing]
            C2[Embedding]
            C3[Retrieval]
            C4[Conversation Summary]
            C5[Deep Research]
        end

        subgraph Infra[基础设施层]
            D1[(PostgreSQL)]
            D2[(Milvus)]
            D3[(Redis)]
            D4[(MinIO)]
            D5[(GROBID)]
        end

        Access --> Orchestration --> Capability --> Infra
```

## 快速复现

### 1) 前置要求

- Docker 24+
- Docker Compose v2
- 至少 8GB 内存（推荐 16GB）

### 2) 克隆仓库

```bash
git clone <your-repo-url>
cd scholar-agent
```

### 3) 创建环境变量文件

当前仓库没有 .env.example，请手动创建 .env：

```bash
cat > .env << 'EOF'
APP_ENV=development
LOG_LEVEL=INFO
API_PREFIX=/api/v1

# Database
DATABASE_URL=postgresql+asyncpg://scholar:scholar_dev_123@postgres:5432/scholar_agent
REDIS_URL=redis://redis:6379/0

# Milvus
MILVUS_HOST=milvus
MILVUS_PORT=19530

# MinIO
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=scholar_minio
MINIO_SECRET_KEY=scholar_minio_123
MINIO_BUCKET=scholar-papers

# GROBID
GROBID_URL=http://grobid:8070

# LLM Keys (至少填一个)
DASHSCOPE_API_KEY=
DEEPSEEK_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=

# Optional
OLLAMA_BASE_URL=http://host.docker.internal:11434
DEFAULT_LLM_MODEL=qwen3-max-2026-01-23
LIGHT_LLM_MODEL=qwen3.5-plus
STRONG_LLM_MODEL=qwen3.5-122b-a10b
EOF
```

建议：

- 不要在 .env 同一行末尾写注释，避免 key 读取异常
- 若系统开启代理，请配置 NO_PROXY=127.0.0.1,localhost

### 4) 首次运行注意模型缓存

docker-compose.yml 中启用了离线模式，且模型缓存目录是本机绝对路径挂载。首次复现请至少完成以下一项：

- 把 compose 文件中的模型缓存路径改成你的机器实际路径
- 或临时关闭 backend/celery 的 TRANSFORMERS_OFFLINE 与 HF_DATASETS_OFFLINE，先完成模型下载

### 5) 启动服务

```bash
docker compose up -d --build
```

### 6) 健康检查

```bash
curl http://localhost:8000/
curl http://localhost:8000/api/v1/health
```

默认访问入口：

- API Docs: http://localhost:8000/docs
- Web Frontend: http://localhost:8501
- MinIO Console: http://localhost:9001

## 使用方式

### Web 前端

访问 http://localhost:8501，主要页面包括：

- Chat: 知识库增强对话
- Documents: 上传与检索论文
- Notes: 结构化研究笔记管理
- Research: 深度研究流式分析
- Dashboard: 研究状态与关系看板

### CLI

```bash
# 对话（自动检索知识库）
python -m cli.main chat "对比 FlashAttention 与 Linear Attention"

# 上传 PDF
python -m cli.main upload ./paper.pdf

# 查看异步任务状态
python -m cli.main tasks <task_id>

# 搜索文档
python -m cli.main search "multimodal learning"

# 查看可用模型（按已配置 API key 动态显示）
python -m cli.main models
```

### API 最小工作流

```bash
# 1) 上传文档
curl -X POST "http://localhost:8000/api/v1/documents/upload?doc_type=paper&language=en" \
    -F "file=@./paper.pdf"

# 2) 查询异步任务状态
curl "http://localhost:8000/api/v1/documents/tasks/<task_id>"

# 3) 语义检索
curl -X POST "http://localhost:8000/api/v1/documents/search" \
    -H "Content-Type: application/json" \
    -d '{"query":"robust control", "top_k":5, "doc_type":"all"}'

# 4) 代理对话
curl -X POST "http://localhost:8000/api/v1/proxy/chat" \
    -H "Content-Type: application/json" \
    -d '{"query":"总结知识库里关于Lyapunov稳定性的方法", "model":"qwen3.5-plus"}'
```

### MCP (Claude Desktop)

将 mcp_server/config.json 合并到 Claude Desktop 配置，并修改：

- cwd: 项目实际路径
- PYTHONPATH: 项目实际路径

可调用工具：

- search_papers
- get_paper_detail
- save_note
- search_notes
- get_task_status

## 本地开发

推荐方式：基础设施容器化，应用进程本地运行。

### 1) 启动基础设施

```bash
docker compose up -d postgres redis milvus minio grobid
```

### 2) 创建 Python 环境并安装依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3) 修改 .env 为本地地址

```env
DATABASE_URL=postgresql+asyncpg://scholar:scholar_dev_123@localhost:5432/scholar_agent
REDIS_URL=redis://localhost:6379/0
MILVUS_HOST=localhost
MINIO_ENDPOINT=localhost:9010
GROBID_URL=http://localhost:8070
```

### 4) 分别启动应用进程

```bash
# terminal 1
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

# terminal 2
celery -A backend.tasks.celery_app worker --loglevel=info --concurrency=2

# terminal 3
streamlit run frontend/app.py --server.port 8501
```

## 常见问题

### 文档上传后任务一直 pending/failed

- 检查 celery worker 是否在运行
- 检查 GROBID 健康状态: http://localhost:8070/api/isalive
- 检查 embedding 模型是否可用（离线模式必须有本地缓存）

### 模型列表为空或聊天报 provider 未配置

- 至少配置一个有效 API key
- 占位值（如 sk-xxx）会被识别为未配置

### 本地接口出现 502 或连接异常

- 若系统开启代理，给 localhost 配置 NO_PROXY
- 容器内访问依赖要使用服务名（postgres/redis/milvus/minio/grobid），不要使用 localhost

## 项目结构

```text
backend/
    api/                # 文档、对话、笔记、研究 API
    skills/             # paper_parser / embedding / retrieval / conversation_summary
    tasks/              # Celery 异步任务
    services/           # MinIO、Milvus、GROBID、Notes 服务层
frontend/             # Streamlit 页面
cli/                  # 命令行工具
mcp_server/           # MCP Server for Claude Desktop
scripts/              # DB 初始化与评测脚本
```

## 路线图

- Phase 1: 文档上传、解析、检索闭环
- Phase 2: 代理编排、多模型适配、笔记系统
- Phase 3: 深度研究流程、关系发现、研究看板
- Phase 4: 前端升级与部署工程化

## 贡献与开源

欢迎提交 Issue 和 PR。建议在提交前执行：

```bash
pytest backend/tests -q
```

当前仓库尚未包含 LICENSE 文件，建议开源前补充（例如 MIT）。
