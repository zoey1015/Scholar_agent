# ScholarAgent

科研知识管理与 AI 辅助研究系统 —— AI 无关的知识后端。

## 核心理念

解决科研工作者在 AI 对话框中讨论问题时的痛点：附件上传有上限、讨论成果散落各处。

系统定位为**知识后端**，不绑定任何单一 AI 平台，按平台能力自动降级：

| 通道 | 适用场景 | 体验 |
|------|---------|------|
| MCP Server | Claude 桌面端 | AI 自动调用 Tool，全自动 |
| 代理编排 API | GPT / Deepseek / Ollama 等 | 系统编排 RAG Pipeline，全自动 |
| CLI 工具 | 终端 | `scholar chat --model deepseek "问题"` |
| Web 面板 | 浏览器 | 文档管理、质量仪表盘、链路追踪 |

## 快速开始

```bash
# 1. 克隆项目
git clone <repo-url> && cd scholar-agent

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 API Keys

# 3. 启动所有服务
docker-compose up -d

# 4. 访问
# - API 文档: http://localhost:8000/docs
# - Web 面板: http://localhost:8501
```

### 使用 MCP Server（Claude 桌面端）

将 `mcp_server/config.json` 的内容合并到 Claude Desktop 的配置文件中，修改 `cwd` 为项目实际路径。

### 使用 CLI

```bash
# 安装 CLI 依赖
pip install -r requirements.txt

# 带知识库上下文对话
python -m cli.main chat "transformer attention 优化方法"

# 指定模型
python -m cli.main chat --model deepseek-chat "对比 FlashAttention"

# 搜索知识库
python -m cli.main search "多模态学习"

# 上传论文
python -m cli.main upload paper.pdf
```

## 技术架构

```
接入层:   MCP Server | REST API | 代理编排 API | CLI
            ↓           ↓            ↓           ↓
核心层:   LangGraph 调度 | Skill 注册中心 | 记忆管理器
            ↓
能力层:   论文解析 | 专利解析 | 混合检索 | 对话总结 | 写作辅助
            ↓
基础设施: PostgreSQL | Milvus | Redis | MinIO
```

## 技术栈

- **后端**: FastAPI + LangGraph + Celery
- **MCP**: mcp-python-sdk
- **数据库**: PostgreSQL (结构化 + BM25) + Milvus (向量)
- **Embedding**: BAAI/bge-m3 (多语言)
- **LLM**: Claude / GPT / Deepseek / Ollama (统一适配层)
- **前端**: Streamlit (MVP)

## 项目结构

```
scholar-agent/
├── mcp_server/          # MCP Server（Claude 接入）
├── backend/
│   ├── api/             # REST API + 代理编排 API
│   ├── agent/           # LangGraph 状态机
│   ├── skills/          # 可插拔 Skill 模块（核心）
│   ├── llm_adapters/    # LLM 统一适配层
│   ├── models/          # ORM 数据模型
│   ├── tasks/           # Celery 异步任务
│   └── db/              # 数据库连接
├── frontend/            # Streamlit 管理面板
├── cli/                 # CLI 命令行工具
└── scripts/             # 数据库初始化、评估脚本
```

## 开发路线

- **Phase 1**: 论文上传 → RAG 检索 → MCP 接入 Claude ✅ (骨架已搭建)
- **Phase 2**: 代理编排 + LLM 适配层 + 对话总结 + 中文专利
- **Phase 3**: 混合检索 + 质量控制 + 链路追踪 + 写作辅助
- **Phase 4**: React 前端 + 知识图谱 + 部署

## License

MIT
