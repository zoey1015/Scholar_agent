# ScholarAgent —— 科研知识管理与 AI 辅助研究系统

## 项目定位

面向科研工作者的智能研究助手，集论文管理、知识沉淀、文献发现、写作辅助于一体的 Agent 系统。区别于简单的 RAG 问答机器人，本系统以**可插拔 Skill 架构**为核心，具备任务编排、记忆管理、可观测性等完整的 Agent 工程能力。

**核心设计理念：知识后端，而非对话替代品。**

用户的核心痛点是：在 Claude / ChatGPT / Deepseek 等各类 AI 对话框中讨论科研问题时，附件上传有上限，讨论成果散落在各个对话窗口中无法沉淀。因此，本系统不试图替代现有 AI 的对话能力，而是作为 **AI 无关的知识后端**——优先 MCP，辅以代理编排 API / CLI 的多通道接入策略，按平台能力自动降级：支持 MCP 则全自动 Tool 调用，不支持则通过代理编排层调用任意模型 API，实现"任何模型 + 知识库"的组合。同时提供独立 Web 前端做知识管理和可视化。

**多语言支持：** 学术论文以英文为主，专利文献以中文为主，系统全链路支持中英双语（Embedding 模型、解析工具、检索与生成均覆盖双语场景）。

---

## 一、功能需求

### 1.1 论文与专利知识库管理

**核心能力：** 上传论文 PDF / 专利文档，系统自动解析、向量化、入库，支持语义检索。

**详细需求：**
- 支持批量上传 PDF，自动识别文档类型（英文学术论文 / 中文专利 / 中文论文）
- 英文学术论文使用 GROBID / Nougat 解析，提取标题、作者、摘要、章节、公式、引用列表
- 中文专利文档单独处理（专利 PDF 格式与学术论文差异较大），提取权利要求、说明书、附图说明等结构化字段
- 解析结果以结构化形式存入 PostgreSQL（元信息）和向量数据库（语义 Embedding）
- Embedding 模型使用多语言模型（如 BAAI/bge-m3），支持中英文混合检索
- 支持按关键词、语义相似度、作者、年份、标签、文档类型等多维度检索
- 支持对论文/专利进行手动标签分类和标注

**入库质量控制（关键）：**
- 建立解析质量指标：解析成功率、缺字段率（标题/摘要/作者缺失）、分块异常率
- 解析失败或质量不达标的文档标记为"待人工审核"，不直接进入检索索引
- 提供入库质量仪表盘，可视化展示各项指标趋势

**检索策略（关键）：**
- 采用**混合检索**：关键词检索（BM25）+ 向量语义检索，综合排序
- 加入 **Reranking** 重排序层（如 bge-reranker），提升检索精度
- 专利场景尤其需要关键词检索兜底——专利文本中的专业术语和编号，纯向量检索容易误召回
- 支持按 doc_type、language、year 等字段做检索前过滤

### 1.2 对话总结与知识沉淀

**核心能力：** 每次与 AI 的讨论结束后，自动/手动触发总结，生成结构化研究笔记存入知识库。

**详细需求：**
- 接收对话历史（支持多来源：MCP 对话、代理编排对话、手动粘贴、平台导出 JSON）
- 生成**结构化研究笔记**，固定输出以下字段（JSON + Markdown 双格式）：

```json
{
  "title": "笔记标题",
  "date": "2025-03-26",
  "source_platform": "claude",
  "research_questions": ["本次讨论的核心问题"],
  "hypotheses": ["提出的假设"],
  "innovations": [
    {"point": "创新点描述", "status": "待验证", "evidence": "支持依据"}
  ],
  "conclusions": ["达成的结论"],
  "open_questions": ["未解决的问题"],
  "experiments_todo": ["待验证的实验方案"],
  "cited_doc_ids": ["doc_uuid_1", "doc_uuid_2"],
  "cited_chunk_ids": ["chunk_uuid_1"]
}
```

- **笔记与文献的双向关联（关键）**：每条笔记关联到具体的文献片段 ID（chunk level），而非仅关联文档 ID。后续写作引用时可以精确溯源到原文段落
- 研究笔记同样做向量化处理，纳入 RAG 检索范围
- 支持对历史笔记的关联分析——识别不同时间点讨论中的关联思路
- 按时间线归档，支持按研究方向、创新点状态等维度筛选

### 1.3 文献发现与推荐

**核心能力：** 基于用户已有知识库内容，主动推荐相关论文。

**详细需求：**
- 对接 Semantic Scholar API / arXiv API，根据关键词或已有论文检索相关文献
- 基于用户知识库中的论文和讨论记录，生成个性化推荐
- 支持按研究方向设置"关注流"，定期推送新论文
- 新检索到的论文可一键加入知识库

### 1.4 写作辅助

**核心能力：** 基于知识库内容，辅助撰写学术论文、专利说明书等文档。

**详细需求：**
- 支持多种写作任务类型：Related Work 综述、摘要生成、专利权利要求书、实验方法描述等
- 根据写作任务自动从知识库检索相关论文段落、实验数据、讨论结论作为上下文
- 生成初稿并支持迭代修改
- 自动生成参考文献列表（BibTeX 格式）
- 支持学术写作规范检查（用词、格式、逻辑连贯性）

### 1.5 知识图谱与可视化

**核心能力：** 自动分析论文间关系，生成可视化知识图谱。

**详细需求：**
- 自动提取论文间的引用关系
- 识别概念和方法的关联关系
- 生成交互式知识图谱，支持缩放、筛选、路径查找
- 支持按时间线展示研究方向的发展脉络

### 1.6 研究进度追踪

**核心能力：** 基于知识积累自动生成研究进度报告。

**详细需求：**
- 统计知识库增长（论文数量、笔记数量、覆盖的主题）
- 基于对话总结自动识别研究阶段和关键里程碑
- 生成周/月研究进度报告，支持导出

### 1.7 多通道接入设计

**设计原则：** 系统定位为**AI 无关的知识后端**，不绑定任何单一 AI 平台。通过"优先 MCP，辅以代理编排 / CLI 的多通道接入"策略，按平台能力做体验降级：支持 MCP → 全自动 Tool 调用；不支持 MCP 但有 API → 代理编排全自动；两者都不支持 → CLI 手动触发。

#### 通道一：MCP Server（Claude 原生集成，全自动）

**适用平台：** Claude 桌面端、Claude Code、及未来支持 MCP 的客户端
**体验等级：** ★★★★★（AI 自动调用 Tool，用户无需任何额外操作）

将系统核心能力封装为 MCP Server，暴露标准化的 Tool 供支持 MCP 协议的客户端直接调用。

**暴露的 MCP Tools：**

| Tool 名称 | 功能 | 典型调用场景 |
|:---|:---|:---|
| `search_papers` | 从知识库语义检索论文/专利 | "帮我找一下关于 attention 优化的论文" |
| `get_paper_detail` | 获取指定文档的详细内容 | "把 xxx 那篇论文的方法部分调出来" |
| `search_notes` | 检索历史研究笔记 | "我之前讨论过的关于 xxx 的创新点是什么" |
| `save_note` | 将当前对话总结存入知识库 | "把我们这次讨论总结一下保存起来" |
| `add_paper` | 通过 URL/DOI 将论文加入知识库 | "把这篇 arXiv 论文加到我的知识库" |
| `get_writing_context` | 根据写作任务拉取相关上下文 | "我要写 Related Work，帮我整理相关论文" |
| `get_task_status` | 查询异步任务状态（解析、向量化等） | "我刚上传的论文解析好了吗" |

**用户体验流程：**
1. 用户在 Claude 桌面端配置连接本系统的 MCP Server
2. 正常在 Claude 对话框中讨论科研问题
3. 需要引用论文时，Claude 自动调用 `search_papers` 从知识库检索，无需手动上传附件
4. 讨论结束后，用户说"保存笔记"，Claude 调用 `save_note` 将总结存入知识库
5. 下次新对话时，通过 `search_notes` 检索历史讨论成果，实现跨对话的知识延续

#### 通道二：代理编排 API（任意模型，全自动）

**适用平台：** GPT、Deepseek、Kimi、Grok、开源模型（通过 vLLM / Ollama 部署）等所有不支持 MCP 的模型
**体验等级：** ★★★★☆（全自动，但通过系统的编排层而非 AI 平台原生调用）

对不支持 MCP 的模型，系统提供一层**代理编排服务**：接收用户问题，自动完成"检索知识库 → 拼接上下文到提示词 → 调用目标模型 API → 回写笔记"的完整 RAG Pipeline。

**与 MCP 通道的核心区别：**
- MCP 场景：Claude 自己决定什么时候调 Tool、调哪个 Tool（AI 侧决策）
- 代理编排场景：系统的 LangGraph 状态机决定检索策略和上下文拼接（系统侧决策）
- 两条路径**共享 Skill 层**，但编排逻辑分开

**代理编排 API：**

```
POST /api/v1/proxy/chat
{
    "query": "transformer 中 multi-head attention 的计算复杂度优化有哪些方法？",
    "model": "deepseek-chat",          // 目标模型（支持 claude/gpt-4o/deepseek/kimi/ollama:qwen2 等）
    "auto_retrieve": true,             // 是否自动检索知识库
    "auto_save_note": false,           // 对话结束后是否自动保存笔记
    "retrieve_options": {
        "top_k": 5,
        "doc_type": "all"
    }
}
```

**内部编排流程（LangGraph 状态机）：**
1. 接收用户 query
2. `RetrievalSkill`：混合检索知识库，获取相关文献片段
3. 上下文拼接：将检索结果格式化后注入 system prompt
4. 模型调用：通过统一的 LLM 适配层调用目标模型 API
5. （可选）`ConversationSummarySkill`：自动总结对话并存入知识库
6. 返回模型回答 + 引用的文献来源

**LLM 适配层设计：**

```python
class LLMAdapter:
    """统一的模型调用接口，屏蔽不同 API 的差异"""

    adapters = {
        "claude": AnthropicAdapter,
        "gpt": OpenAIAdapter,
        "deepseek": DeepseekAdapter,
        "kimi": MoonshotAdapter,
        "ollama": OllamaAdapter,       # 本地开源模型
    }

    async def chat(self, model: str, messages: list, **kwargs) -> str:
        provider = self._resolve_provider(model)
        adapter = self.adapters[provider]()
        return await adapter.chat(model, messages, **kwargs)
```

#### 通道三：Web 前端（知识管理面板）

**适用平台：** 独立访问
**体验等级：** ★★★★★（完整的管理功能）

独立的 Web 界面，专注于 MCP 和代理编排不方便处理的管理类操作：
- 批量上传和管理论文/专利文档
- 查看解析状态、入库质量指标、任务进度
- 研究笔记的浏览、编辑和关联分析
- 知识图谱的交互式可视化
- Skill 执行链路的可视化追踪（可观测性面板）
- 系统配置和管理

#### 通道四：CLI 工具（开发者 / 日常快捷操作）

**适用平台：** 终端环境
**体验等级：** ★★★★☆（一行命令完成带上下文的对话，调用代理编排 API）

CLI 工具底层调用代理编排 API，实现一步到位的知识库增强对话：

```bash
# 带知识库上下文的对话（调用代理编排 API，默认用 Claude）
$ scholar chat "transformer attention 优化方法有哪些？"

# 指定模型
$ scholar chat --model deepseek "对比一下 FlashAttention 和 Linear Attention"

# 搜索知识库
$ scholar search "多模态学习"

# 保存对话总结
$ scholar save-note --input chat_export.json

# 上传论文
$ scholar upload paper.pdf

# 查看任务状态
$ scholar tasks --status pending

# 获取写作上下文
$ scholar writing-context --task "related_work" --topic "视觉语言模型"
```

#### 接入降级策略总览

```
平台支持 MCP？
  ├── 是 → 通道一：MCP Server（AI 侧决策，全自动）
  └── 否 → 平台有 API？
              ├── 是 → 通道二：代理编排 API（系统侧决策，全自动）
              └── 否 → 通道四：CLI 手动触发
                        或通道三：Web 前端操作
```

#### 接入优先级与开发顺序

| 优先级 | 通道 | 开发阶段 | 理由 |
|:---|:---|:---|:---|
| P0 | REST API（基础能力层） | Phase 1 | 所有其他通道的基础 |
| P0 | MCP Server | Phase 1 | 面试核心亮点，MCP 是 Agent 生态热点 |
| P0 | Web 管理面板 | Phase 1 | 文档上传和管理的必要界面 |
| P1 | 代理编排 API + LLM 适配层 | Phase 2 | 覆盖所有非 MCP 模型，开发量适中 |
| P1 | CLI 工具 | Phase 2 | 调用代理编排 API，开发量小 |

### 1.8 权限与隐私

**设计原则：** 科研内容高度敏感，系统需从 Day1 就考虑数据隔离与安全。

- 所有数据严格按 `user_id` 隔离，用户之间不可见
- 日志中对论文内容、对话内容做脱敏处理（仅记录操作类型和元信息）
- 支持用户删除自己的数据（论文、笔记、对话历史、向量索引），满足"可删除"要求
- MCP Server 和 REST API 均需认证（API Key / JWT），防止未授权访问
- 向量数据库按 user_id 做 namespace 隔离或 partition 隔离

### 1.9 异步任务状态可见性

**设计原则：** 论文解析、向量化等耗时操作必须异步执行，且所有通道都能查询任务状态。

- 每个异步任务生成唯一 `task_id`，可通过 REST API 查询状态（pending / processing / success / failed）
- MCP Server 暴露 `get_task_status` Tool，用户在 Claude 对话中也能查看上传进度
- Web 前端实时展示任务队列和进度条
- 任务失败时记录错误详情，支持手动重试

---

## 二、Skill 架构设计

### 2.1 Skill 接口规范

所有 Skill 遵循统一的抽象基类，实现可插拔、可编排的能力模块：

```python
from abc import ABC, abstractmethod
from pydantic import BaseModel
from typing import Any, Optional
from enum import Enum

class SkillStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"  # 部分成功，降级处理

class SkillContext(BaseModel):
    """Skill 执行上下文，在 Skill 间传递"""
    user_id: str
    session_id: str
    query: str                          # 用户原始输入
    retrieved_docs: list[dict] = []     # RAG 检索结果
    conversation_history: list[dict] = []
    metadata: dict[str, Any] = {}       # 扩展字段

class SkillResult(BaseModel):
    """Skill 执行结果"""
    status: SkillStatus
    data: Any                           # 输出数据
    message: str = ""
    artifacts: list[str] = []           # 生成的文件路径
    next_skill_hint: Optional[str] = None  # 建议下一步调用的 Skill

class BaseSkill(ABC):
    name: str
    description: str       # 供 Agent 调度器理解何时调用
    version: str = "1.0.0"

    @abstractmethod
    async def execute(self, context: SkillContext) -> SkillResult:
        """执行 Skill 的核心逻辑"""
        ...

    @abstractmethod
    def get_input_schema(self) -> dict:
        """返回输入参数的 JSON Schema"""
        ...

    @abstractmethod
    def get_output_schema(self) -> dict:
        """返回输出数据的 JSON Schema"""
        ...

    def validate_input(self, context: SkillContext) -> bool:
        """输入参数校验，子类可覆写"""
        return True
```

### 2.2 Skill 清单

| Skill 名称 | 职责 | 输入 | 输出 |
|:---|:---|:---|:---|
| `PaperParserSkill` | 解析英文学术论文 PDF（GROBID） | PDF 文件路径 | 结构化论文对象 |
| `PatentParserSkill` | 解析中文专利文档 PDF | PDF 文件路径 | 结构化专利对象（权利要求、说明书等） |
| `DocTypeDetectorSkill` | 自动识别文档类型和语言 | PDF 文件路径 | 文档类型 + 语言标识，路由到对应 Parser |
| `QualityCheckSkill` | 入库质量校验（字段完整性、分块合理性） | 解析结果 | 质量评分 + 异常标记 |
| `EmbeddingSkill` | 文本向量化（bge-m3 多语言） | 文本块列表 | embedding ID 列表 |
| `RetrievalSkill` | **混合检索**：BM25 + 向量 + Rerank | 查询文本 + 检索参数 | 重排序后的文档片段列表 |
| `ConversationSummarySkill` | 总结对话，输出结构化研究笔记 | 对话历史 | 结构化 JSON（含 chunk 级引用关联） |
| `LiteratureSearchSkill` | 外部文献检索 | 关键词 / 论文 ID | 论文元信息列表 |
| `WritingAssistSkill` | 辅助学术写作（论文/专利） | 写作任务类型 + 上下文 | 初稿文本 |
| `KnowledgeGraphSkill` | 构建/更新知识图谱 | 论文元信息 + 引用关系 | 图谱数据（节点 + 边） |
| `ProgressReportSkill` | 生成研究进度报告 | 时间范围 | 报告文档 |

### 2.3 Skill 注册与发现

```python
class SkillRegistry:
    """Skill 注册中心，支持动态注册和发现"""

    def __init__(self):
        self._skills: dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill):
        self._skills[skill.name] = skill

    def get(self, name: str) -> BaseSkill:
        return self._skills[name]

    def list_skills(self) -> list[dict]:
        """返回所有 Skill 的名称和描述，供 Agent 选择"""
        return [
            {"name": s.name, "description": s.description}
            for s in self._skills.values()
        ]

    def get_tool_definitions(self) -> list[dict]:
        """生成 LLM function calling 的 tools 定义"""
        return [
            {
                "type": "function",
                "function": {
                    "name": skill.name,
                    "description": skill.description,
                    "parameters": skill.get_input_schema()
                }
            }
            for skill in self._skills.values()
        ]
```

---

## 三、系统架构

### 3.1 整体架构总览

```
┌──────────────────────────────────────────────────────────────┐
│                     Client Access 接入层                      │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ Claude 桌面端│  │ Web 管理面板 │  │  CLI 工具        │   │
│  │ (via MCP)    │  │ (Streamlit/  │  │  (Typer)         │   │
│  │              │  │  React)      │  │                  │   │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘   │
│         │                 │                    │             │
├─────────┼─────────────────┼────────────────────┼─────────────┤
│         ▼                 ▼                    ▼             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │  MCP Server  │  │  REST API    │  │  代理编排 API    │   │
│  │  (stdio/SSE) │  │  (FastAPI)   │  │  (Proxy Chat)    │   │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘   │
│         │                 │                    │             │
│         │                 │          ┌─────────▼──────────┐  │
│         │                 │          │  LLM 适配层        │  │
│         │                 │          │  Claude│GPT│DS│... │  │
│         │                 │          └─────────┬──────────┘  │
│         └────────┬────────┴────────────────────┘             │
│                  ▼                                           │
├──────────────────────────────────────────────────────────────┤
│                    Agent Core 核心调度层                      │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────┐     │
│  │ LangGraph  │  │  Skill 注册  │  │   记忆管理器     │     │
│  │ 状态机调度 │  │  与编排中心  │  │ (短期+长期记忆)  │     │
│  └────────────┘  └──────────────┘  └──────────────────┘     │
│                  ┌──────────────┐                            │
│                  │  模型路由器  │                            │
│                  │ (任务→模型  │                            │
│                  │  分级映射)  │                            │
│                  └──────────────┘                            │
├──────────────────────────────────────────────────────────────┤
│                      Skill 能力层（共享）                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │  论文    │ │  专利    │ │  对话    │ │  文献    │       │
│  │  解析    │ │  解析    │ │  总结    │ │  检索    │       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │  混合    │ │  写作    │ │  知识    │ │  质量    │       │
│  │  检索    │ │  辅助    │ │  图谱    │ │  校验    │       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
├──────────────────────────────────────────────────────────────┤
│                      Infrastructure 基础设施层               │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │PostgreSQL│ │  Milvus  │ │  Redis   │ │  MinIO   │       │
│  │+全文检索 │ │ 向量存储 │ │ 缓存+   │ │ 文件    │       │
│  │          │ │          │ │ 任务队列 │ │ 存储    │       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
├──────────────────────────────────────────────────────────────┤
│                      Observability 可观测层                  │
│  ┌──────────────────┐  ┌─────────────────────┐              │
│  │  LangSmith /     │  │  Prometheus +       │              │
│  │  自研 Trace      │  │  Grafana 监控       │              │
│  └──────────────────┘  └─────────────────────┘              │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 核心模块详解

#### 3.2.1 Agent 调度核心（基于 LangGraph）

采用 LangGraph 实现有状态的 Agent 调度，而非简单的 LangChain AgentExecutor。原因是 LangGraph 支持复杂的条件分支、循环、人工介入等场景，更适合科研助手的多步推理需求。

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, Annotated

class AgentState(TypedDict):
    messages: list                 # 对话历史
    current_task: str              # 当前任务类型
    skill_results: dict            # 各 Skill 的执行结果
    retrieved_context: list        # RAG 检索到的上下文
    plan: list[str]                # 任务规划（多步执行时）
    step_index: int                # 当前执行到第几步

# 核心节点定义
def router_node(state: AgentState) -> str:
    """意图识别 + 路由：决定调用哪个 Skill 或组合"""
    ...

def retrieval_node(state: AgentState) -> AgentState:
    """RAG 检索节点"""
    ...

def skill_executor_node(state: AgentState) -> AgentState:
    """Skill 执行节点，根据路由结果调用对应 Skill"""
    ...

def response_generator_node(state: AgentState) -> AgentState:
    """基于 Skill 结果 + 检索上下文生成最终回答"""
    ...

# 构建状态图
graph = StateGraph(AgentState)
graph.add_node("router", router_node)
graph.add_node("retrieval", retrieval_node)
graph.add_node("skill_executor", skill_executor_node)
graph.add_node("response_generator", response_generator_node)

graph.set_entry_point("router")
graph.add_conditional_edges("router", route_decision, {
    "need_retrieval": "retrieval",
    "direct_skill": "skill_executor",
    "simple_chat": "response_generator",
})
graph.add_edge("retrieval", "skill_executor")
graph.add_edge("skill_executor", "response_generator")
graph.add_edge("response_generator", END)
```

#### 3.2.2 记忆管理器

```python
class MemoryManager:
    """
    管理 Agent 的短期记忆（对话上下文）和长期记忆（知识库）
    """
    def __init__(self, vector_store, pg_client, redis_client):
        self.vector_store = vector_store   # 长期语义记忆
        self.pg = pg_client                # 结构化长期记忆
        self.redis = redis_client          # 短期会话记忆

    async def get_session_context(self, session_id: str) -> list:
        """获取当前会话的对话历史（短期记忆）"""
        ...

    async def search_long_term(self, query: str, top_k: int = 5) -> list:
        """语义检索长期记忆（论文 + 研究笔记）"""
        ...

    async def save_to_long_term(self, content: str, metadata: dict):
        """将新知识写入长期记忆"""
        ...
```

#### 3.2.3 模型路由层

```python
class ModelRouter:
    """
    不同任务使用不同规格的模型，平衡效果与成本

    路由策略：
    - 意图识别 / 简单分类 → 轻量模型（如 Claude Haiku）
    - 论文总结 / 写作辅助 → 强模型（如 Claude Sonnet）
    - 复杂推理 / 多步规划 → 最强模型（如 Claude Opus / GPT-4o）
    """
    def __init__(self, config: dict):
        self.model_map = config  # task_type -> model_name 的映射

    def get_model(self, task_type: str) -> str:
        return self.model_map.get(task_type, "default_model")
```

#### 3.2.4 MCP Server 模块

MCP Server 是系统对外暴露能力的核心接口，将内部 Skill 封装为标准 MCP Tool，供 Claude 等客户端调用。

```python
from mcp.server import Server
from mcp.types import Tool, TextContent

app = Server("scholar-agent")

@app.list_tools()
async def list_tools() -> list[Tool]:
    """注册 MCP Tools，每个 Tool 对应一个或多个 Skill 的组合"""
    return [
        Tool(
            name="search_papers",
            description="从知识库中语义检索论文和专利，支持中英文查询",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词或自然语言描述"},
                    "doc_type": {"type": "string", "enum": ["all", "paper", "patent"], "default": "all"},
                    "top_k": {"type": "integer", "default": 5}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="save_note",
            description="将当前对话的讨论总结保存为研究笔记，存入知识库",
            inputSchema={
                "type": "object",
                "properties": {
                    "conversation": {"type": "string", "description": "需要总结的对话内容"},
                    "title": {"type": "string", "description": "笔记标题（可选）"}
                },
                "required": ["conversation"]
            }
        ),
        # ... 其他 Tools
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """
    Tool 调用入口，路由到对应的 Skill 执行
    MCP Tool 是面向外部客户端的接口粒度
    Skill 是面向内部的能力粒度
    一个 Tool 可能编排多个 Skill
    """
    if name == "search_papers":
        # 编排: RetrievalSkill → 格式化输出
        context = SkillContext(query=arguments["query"], ...)
        result = await skill_registry.get("retrieval").execute(context)
        return [TextContent(type="text", text=format_search_results(result))]

    elif name == "save_note":
        # 编排: ConversationSummarySkill → EmbeddingSkill
        context = SkillContext(conversation_history=arguments["conversation"], ...)
        summary = await skill_registry.get("conversation_summary").execute(context)
        await skill_registry.get("embedding").execute(...)  # 向量化存储
        return [TextContent(type="text", text=f"已保存研究笔记：{summary.data['title']}")]
```

**MCP Tool 与 Skill 的关系：**
- MCP Tool 是面向 Claude 等外部客户端的**粗粒度接口**
- Skill 是内部的**细粒度能力模块**
- 一个 MCP Tool 可能编排调用多个 Skill（如 `save_note` 串联了总结 + 向量化）
- Web 前端的 API 也复用同一套 Skill，保持逻辑统一

### 3.3 数据库设计

#### PostgreSQL 核心表

```sql
-- 文档元信息表（论文 + 专利统一存储）
CREATE TABLE documents (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL,           -- 数据隔离
    doc_type      VARCHAR(20) NOT NULL,    -- "paper" / "patent"
    language      VARCHAR(10) NOT NULL,    -- "en" / "zh" / "mixed"
    title         TEXT NOT NULL,
    authors       JSONB,                   -- ["Author1", "Author2"]
    abstract      TEXT,
    year          INTEGER,
    source        VARCHAR(50),             -- "arxiv" / "upload" / "semantic_scholar" / "cnipa"
    external_id   VARCHAR(100),            -- arXiv ID / DOI / 专利号
    tags          TEXT[],
    file_path     VARCHAR(500),            -- MinIO 中的文件路径
    parsed_data   JSONB,                   -- 解析的结构化内容
    parse_status  VARCHAR(20) DEFAULT 'pending',  -- "pending" / "processing" / "success" / "failed" / "needs_review"
    quality_score JSONB,                   -- {"field_completeness": 0.9, "chunk_quality": 0.85}
    created_at    TIMESTAMP DEFAULT NOW()
);

-- 文本分块表（chunk 级别，支持精确溯源）
CREATE TABLE chunks (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id   UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index   INTEGER NOT NULL,        -- 在文档中的顺序
    content       TEXT NOT NULL,            -- 分块文本内容
    section_title VARCHAR(200),            -- 所属章节标题
    chunk_type    VARCHAR(30),             -- "abstract" / "method" / "result" / "claim" / "description"
    embedding_id  VARCHAR(100),            -- 向量数据库中的 ID
    token_count   INTEGER,
    created_at    TIMESTAMP DEFAULT NOW()
);

-- 研究笔记表（对话总结，结构化字段）
CREATE TABLE research_notes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL,
    title           VARCHAR(200),
    summary         TEXT,
    innovations     JSONB,                 -- [{"point": "...", "status": "待验证", "evidence": "..."}]
    hypotheses      JSONB,                 -- ["假设1", "假设2"]
    key_questions   JSONB,
    conclusions     JSONB,
    experiments_todo JSONB,                -- ["待验证实验1"]
    source_type     VARCHAR(20),           -- "mcp" / "proxy" / "web" / "cli" / "manual"
    source_platform VARCHAR(20),           -- "claude" / "gpt" / "deepseek" / "kimi" / "other"
    source_id       VARCHAR(100),          -- 关联的对话 ID
    cited_doc_ids   UUID[],               -- 关联的文档 ID（文档级）
    cited_chunk_ids UUID[],               -- 关联的分块 ID（chunk 级，精确溯源）
    created_at      TIMESTAMP DEFAULT NOW()
);

-- 知识图谱关系表
CREATE TABLE knowledge_edges (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL,
    source_id     UUID NOT NULL,           -- 源节点（论文/概念）
    target_id     UUID NOT NULL,           -- 目标节点
    relation_type VARCHAR(50),             -- "cites" / "extends" / "contradicts" / "related"
    weight        FLOAT DEFAULT 1.0,
    metadata      JSONB,
    created_at    TIMESTAMP DEFAULT NOW()
);

-- 对话历史表
CREATE TABLE conversations (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL,
    source_platform VARCHAR(20),           -- "claude" / "gpt" / "deepseek" / "kimi"
    messages      JSONB,                   -- 完整对话记录
    summary_id    UUID REFERENCES research_notes(id),
    created_at    TIMESTAMP DEFAULT NOW()
);

-- 异步任务状态表（所有通道可查询）
CREATE TABLE async_tasks (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL,
    task_type     VARCHAR(50) NOT NULL,    -- "parse_paper" / "embed_chunks" / "generate_summary"
    status        VARCHAR(20) DEFAULT 'pending',  -- "pending" / "processing" / "success" / "failed"
    input_data    JSONB,                   -- 任务输入参数
    result_data   JSONB,                   -- 任务输出结果
    error_message TEXT,                    -- 失败时的错误信息
    retry_count   INTEGER DEFAULT 0,
    created_at    TIMESTAMP DEFAULT NOW(),
    updated_at    TIMESTAMP DEFAULT NOW()
);

-- Agent 执行链路追踪表（可观测性）
CREATE TABLE agent_traces (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL,
    session_id    UUID NOT NULL,
    step_index    INTEGER,
    node_name     VARCHAR(50),             -- "router" / "retrieval" / "skill_executor"
    skill_name    VARCHAR(50),
    input_data    JSONB,
    output_data   JSONB,
    model_used    VARCHAR(50),
    latency_ms    INTEGER,
    token_usage   JSONB,                   -- {"input": 500, "output": 200}
    created_at    TIMESTAMP DEFAULT NOW()
);
```

### 3.4 API 接口设计

```
# === 文档管理 ===
POST   /api/v1/documents/upload         # 上传 PDF（自动识别论文/专利）
GET    /api/v1/documents                 # 文档列表（筛选: doc_type, language, tags, 分页）
GET    /api/v1/documents/{id}            # 文档详情
GET    /api/v1/documents/{id}/chunks     # 获取文档的所有分块
DELETE /api/v1/documents/{id}            # 删除文档（含向量索引清理）
POST   /api/v1/documents/search          # 混合检索（BM25 + 向量 + Rerank）

# === 对话与笔记 ===
POST   /api/v1/chat                     # 对话（Agent 入口，SSE 流式）
POST   /api/v1/notes                    # 创建研究笔记（手动或对话总结触发）
GET    /api/v1/notes                    # 笔记列表（筛选: source_platform, 创新点状态等）
GET    /api/v1/notes/{id}               # 笔记详情（含关联的 chunk 原文）
PUT    /api/v1/notes/{id}               # 编辑笔记
DELETE /api/v1/notes/{id}               # 删除笔记

# === 文献检索 ===
POST   /api/v1/literature/search        # 外部文献检索（Semantic Scholar / arXiv）
POST   /api/v1/literature/recommend     # 基于知识库推荐论文

# === 写作辅助 ===
POST   /api/v1/writing/draft            # 生成写作初稿
POST   /api/v1/writing/refine           # 迭代修改

# === 任务状态（所有通道共享） ===
GET    /api/v1/tasks/{task_id}          # 查询单个任务状态
GET    /api/v1/tasks                    # 当前用户的任务列表（筛选: status, task_type）
POST   /api/v1/tasks/{task_id}/retry    # 手动重试失败任务

# === 系统管理 ===
GET    /api/v1/quality/dashboard         # 入库质量指标仪表盘
GET    /api/v1/graph                    # 获取知识图谱数据
GET    /api/v1/progress/report          # 生成进度报告
GET    /api/v1/skills                   # 列出所有可用 Skill
GET    /api/v1/traces/{session_id}      # 查看 Agent 执行链路

# === 用户数据管理 ===
DELETE /api/v1/user/data                # 删除用户所有数据（GDPR 合规）
GET    /api/v1/user/export              # 导出用户数据
```

### 3.5 异步任务设计

以下操作走 Celery 异步任务队列，不阻塞 API 响应：

| 任务 | 触发方式 | 预估耗时 | 重试策略 |
|:---|:---|:---|:---|
| 论文 PDF 解析 | 上传后自动触发 | 10-60s | 最多重试 3 次，指数退避 |
| 文本向量化 | 解析完成后自动触发 | 5-30s | 最多重试 3 次 |
| 对话总结生成 | 用户手动触发或定时 | 10-30s | 最多重试 2 次 |
| 知识图谱更新 | 新论文入库后触发 | 5-20s | 最多重试 2 次 |
| 文献推荐计算 | 定时任务（每日） | 30-120s | 失败告警，不重试 |
| 进度报告生成 | 用户手动触发 | 15-45s | 最多重试 2 次 |

---

## 四、技术栈选型

| 层级 | 技术选型 | 选型理由 |
|:---|:---|:---|
| Web 框架 | FastAPI | 异步原生、自带 OpenAPI 文档、类型安全 |
| MCP Server | mcp-python-sdk | 官方 Python SDK，支持 stdio / SSE 两种传输 |
| LLM 适配层 | 自研统一接口 | 屏蔽 Claude/GPT/Deepseek/Ollama 等 API 差异 |
| CLI 工具 | Typer | 基于类型提示的现代 CLI 框架，开发快 |
| Agent 框架 | LangGraph | 支持有状态的复杂编排，比 AgentExecutor 更灵活 |
| 向量数据库 | Milvus Lite（开发）→ Milvus（生产） | 开源、性能好、社区活跃 |
| 关键词检索 | PostgreSQL Full-text Search | 混合检索中的 BM25 组件，避免额外引入 ES |
| 关系数据库 | PostgreSQL | 支持 JSONB、全文检索、成熟稳定 |
| 缓存/队列 | Redis + Celery | 会话缓存 + 异步任务的标准方案 |
| 文件存储 | MinIO | S3 兼容的本地对象存储 |
| 论文解析 | GROBID / Nougat | 英文学术论文专用解析器 |
| 专利解析 | PyMuPDF + 自定义规则 | 中文专利 PDF 格式特殊，需定制解析逻辑 |
| Embedding 模型 | BAAI/bge-m3（HuggingFace） | 多语言支持、中英文学术场景均表现优秀 |
| Reranking 模型 | BAAI/bge-reranker-v2-m3 | 混合检索后的重排序，提升精度 |
| LLM | 多模型路由：Claude / GPT / Deepseek / Ollama | 按任务分级 + 代理编排支持任意模型 |
| 前端 | Streamlit（MVP）→ React（正式版） | 快速验证 → 产品化 |
| 可观测性 | LangSmith + 自研 Trace | 链路追踪、效果评估 |
| 容器化 | Docker + docker-compose | 本地开发和部署一致性 |

---

## 五、开发路线图

### Phase 1：核心 MVP（2-3 周）

**目标：** 跑通"上传论文 → RAG 问答 → MCP 接入 Claude"的完整链路

- 搭建 FastAPI 项目骨架（项目结构、配置管理、错误处理）
- 实现 `PaperParserSkill`：英文论文 PDF 上传 + GROBID 解析
- 实现 `EmbeddingSkill`（bge-m3 多语言模型）+ `RetrievalSkill`：向量化 + 语义检索
- 实现基础的 Agent 调度：用户提问 → 检索 → LLM 回答
- **实现 MCP Server**：暴露 `search_papers` 和 `get_paper_detail` 两个基础 Tool
- **在 Claude 桌面端配置并验证 MCP 连接**：实现在 Claude 对话框中直接检索知识库
- 用 Streamlit 搭建基础前端：上传界面 + 论文列表 + 检索测试
- Docker Compose 编排所有服务

**验收标准：** 上传 3-5 篇英文论文后，能在 Claude 对话框中通过 MCP 调用知识库检索，获得相关论文内容

### Phase 2：代理编排 + 多语言 + 知识沉淀（2-3 周）

**目标：** 实现代理编排层覆盖所有模型，补齐对话总结和多语言支持

- **实现代理编排 API**（`/api/v1/proxy/chat`）：检索上下文 → 拼接提示词 → 调用模型 → 返回回答
- **实现 LLM 适配层**：统一接口封装 Claude / GPT / Deepseek / Ollama 等 API 差异
- 实现 `ConversationSummarySkill`：结构化对话总结（固定 JSON 字段 + chunk 级引用关联）
- **扩展 MCP Tools**：`save_note`、`search_notes`、`add_paper`
- **实现 CLI 工具**：`scholar chat`（调用代理编排 API）、`scholar search`、`scholar save-note`
- 实现 `PatentParserSkill`：中文专利 PDF 解析
- 实现 `DocTypeDetectorSkill`：自动识别文档类型，路由到对应 Parser
- 引入 LangGraph 状态图，代理编排场景走系统侧决策的编排流程
- 引入 Celery 异步任务队列（论文解析、向量化等耗时操作）

**验收标准：**
- 能在 Claude 中完成"讨论问题 → 保存笔记 → 下次对话检索历史讨论"的闭环
- 能通过 CLI `scholar chat --model deepseek "问题"` 实现非 Claude 模型 + 知识库的组合
- 能上传中文专利文档并正确解析检索

### Phase 3：工程深度 + 写作辅助（2-3 周）

**目标：** 补充工程亮点和高级功能，提升面试竞争力

- 实现 `WritingAssistSkill`：Related Work 综述、专利权利要求书辅助
- 扩展 MCP Tool：`get_writing_context`（写作时拉取上下文）
- 实现**混合检索**：BM25 关键词检索 + 向量检索 + Reranking 重排序
- 实现 `QualityCheckSkill` + 入库质量控制指标和仪表盘
- 实现执行链路追踪（agent_traces 表 + 可视化展示）
- 实现模型路由层（不同 Skill 用不同模型）
- 实现基础评估体系（检索准确率、总结质量评分）
- 错误处理与降级策略（Skill 失败兜底、外部 API 限流处理）
- 实现 `LiteratureSearchSkill`：对接 Semantic Scholar API
- 编写核心模块的单元测试

**验收标准：** 能在面试中完整演示三条通道（MCP / 代理编排 / CLI）的全链路；混合检索在专利场景下的准确率明显优于纯向量检索；能展示 Agent 决策的追踪链路

### Phase 4：产品化打磨（可选）

- React + TypeScript 前端重构（替代 Streamlit）
- 用户认证系统 + 数据隔离
- `KnowledgeGraphSkill` + 知识图谱可视化
- 研究进度追踪与报告
- 性能优化（检索速度、缓存策略、Embedding 批处理）
- 部署到云服务器

---

## 六、项目目录结构

```
scholar-agent/
├── docker-compose.yml
├── .env.example
├── README.md
│
├── mcp_server/                     # MCP Server（独立进程）
│   ├── __init__.py
│   ├── server.py                   # MCP Server 入口（stdio / SSE）
│   ├── tools.py                    # MCP Tool 定义与注册
│   ├── tool_handlers.py            # Tool 调用处理，编排 Skill
│   └── config.json                 # Claude Desktop MCP 配置示例
│
├── backend/
│   ├── main.py                     # FastAPI 入口
│   ├── config.py                   # 配置管理
│   ├── dependencies.py             # 依赖注入
│   │
│   ├── api/                        # REST API 路由层
│   │   ├── __init__.py
│   │   ├── documents.py            # 文档上传、管理、检索
│   │   ├── proxy.py                # 代理编排 API（/proxy/chat）
│   │   ├── notes.py                # 研究笔记
│   │   ├── tasks.py                # 异步任务状态查询
│   │   ├── literature.py           # 外部文献检索
│   │   ├── writing.py              # 写作辅助
│   │   └── admin.py                # 健康检查、Skill 列表、Trace、质量仪表盘
│   │
│   ├── agent/                      # Agent 核心
│   │   ├── __init__.py
│   │   ├── graph.py                # LangGraph 状态图（代理编排场景）
│   │   ├── router.py               # 意图识别与路由
│   │   ├── memory.py               # 记忆管理器
│   │   └── model_router.py         # 任务→模型分级映射
│   │
│   ├── llm_adapters/               # LLM 适配层（统一多模型接口）
│   │   ├── __init__.py
│   │   ├── base.py                 # LLMAdapter 抽象基类
│   │   ├── anthropic_adapter.py    # Claude API
│   │   ├── openai_adapter.py       # GPT API
│   │   ├── deepseek_adapter.py     # Deepseek API
│   │   └── ollama_adapter.py       # 本地开源模型（Ollama）
│   │
│   ├── skills/                     # Skill 模块（所有通道共享）
│   │   ├── __init__.py
│   │   ├── base.py                 # BaseSkill 抽象类
│   │   ├── registry.py             # SkillRegistry
│   │   ├── paper_parser.py         # 英文论文解析（GROBID）
│   │   ├── patent_parser.py        # 中文专利解析（PyMuPDF）
│   │   ├── doc_type_detector.py    # 文档类型 + 语言自动识别
│   │   ├── quality_check.py        # 入库质量校验
│   │   ├── embedding.py            # 多语言向量化（bge-m3）
│   │   ├── retrieval.py            # 混合检索（BM25 + 向量 + Rerank）
│   │   ├── conversation_summary.py # 结构化对话总结
│   │   ├── literature_search.py
│   │   ├── writing_assist.py
│   │   └── knowledge_graph.py
│   │
│   ├── models/                     # 数据模型（Pydantic + SQLAlchemy）
│   │   ├── __init__.py
│   │   ├── document.py             # 统一文档模型（论文+专利）
│   │   ├── chunk.py                # 文本分块模型
│   │   ├── note.py
│   │   ├── task.py                 # 异步任务模型
│   │   └── trace.py
│   │
│   ├── services/                   # 业务逻辑层
│   │   ├── __init__.py
│   │   ├── document_service.py
│   │   ├── vector_service.py
│   │   └── trace_service.py
│   │
│   ├── tasks/                      # Celery 异步任务
│   │   ├── __init__.py
│   │   ├── celery_app.py
│   │   ├── parse_tasks.py
│   │   └── embedding_tasks.py
│   │
│   ├── db/                         # 数据库连接
│   │   ├── __init__.py
│   │   ├── postgres.py
│   │   ├── milvus.py
│   │   └── redis.py
│   │
│   └── tests/
│       ├── test_skills/
│       ├── test_api/
│       ├── test_mcp/               # MCP Tool 集成测试
│       ├── test_proxy/             # 代理编排集成测试
│       └── test_llm_adapters/      # LLM 适配层测试
│
├── frontend/                       # Streamlit 前端（MVP）
│   ├── app.py
│   ├── pages/
│   │   ├── documents.py            # 论文/专利上传与管理
│   │   ├── notes.py                # 研究笔记浏览
│   │   ├── tasks.py                # 异步任务状态
│   │   ├── quality.py              # 入库质量仪表盘
│   │   └── traces.py               # Agent 执行链路查看
│   └── components/
│
├── cli/                            # 命令行工具（Phase 2）
│   ├── __init__.py
│   ├── main.py                     # CLI 入口（Typer）
│   └── commands/
│       ├── chat.py                 # scholar chat（调用代理编排 API）
│       ├── search.py               # scholar search
│       ├── upload.py               # scholar upload
│       └── note.py                 # scholar save-note
│
└── scripts/                        # 工具脚本
    ├── init_db.sql
    ├── seed_data.py
    └── eval/                       # 评估脚本
        ├── retrieval_eval.py
        └── summary_eval.py
```

---

## 七、面试讲解要点

### 能讲清楚"为什么"比"怎么做"更重要

- 为什么做成"AI 无关的知识后端"？→ 不绑定单一平台，按平台能力做降级（MCP → 代理编排 → CLI）
- 为什么 MCP 和代理编排是两套调度逻辑？→ MCP 场景是 AI 侧决策（Claude 自己选 Tool），代理编排是系统侧决策（LangGraph 编排），决策主体不同
- 为什么共享 Skill 层但分离编排层？→ 能力复用，但编排策略按通道特点各自独立
- 为什么需要 LLM 适配层？→ 屏蔽不同模型 API 的差异，新增模型只需加一个 Adapter
- 为什么 MCP Tool 和 Skill 分两层？→ MCP Tool 是粗粒度的外部接口，Skill 是细粒度的内部能力，一个 Tool 可编排多个 Skill
- 为什么检索用混合策略（BM25 + 向量 + Rerank）而不是纯向量？→ 专利场景的专业术语和编号，纯向量容易误召回
- 为什么用 LangGraph 而不是 AgentExecutor？→ 需要条件分支和多步编排
- 为什么设计 Skill 抽象层？→ 可插拔、可测试、可独立迭代
- 为什么 Embedding 用 bge-m3 而不是纯中文或纯英文模型？→ 科研场景天然中英混合
- 为什么论文和专利分开做 Parser？→ 文档结构差异大，GROBID 擅长学术论文但不适合专利格式
- 为什么笔记要关联到 chunk 级别而不是文档级别？→ 写作引用时需要精确溯源到原文段落
- 为什么需要入库质量控制？→ 垃圾进垃圾出，解析质量直接决定检索效果
- 为什么用异步任务队列？→ 解析和向量化是耗时操作，不能阻塞用户请求
- 为什么做执行链路追踪？→ Agent 系统的调试和效果评估依赖可观测性

### 可以深挖的技术点

- 多通道降级架构（MCP / 代理编排 / CLI 共享 Skill 层，编排层如何分离又协作）
- 代理编排 vs MCP 的调度差异（AI 侧决策 vs 系统侧决策，各自的 LangGraph 状态机设计）
- LLM 适配层设计（如何用统一接口屏蔽 Claude / GPT / Deepseek / Ollama 的 API 差异）
- MCP 协议的设计理念和实现细节（stdio vs SSE 传输、Tool 注册机制、与 Claude 的交互流程）
- RAG 的分块策略和检索优化（分块大小对检索质量的影响、混合检索的权重调优、Reranking）
- 多语言检索的挑战（中英文混合查询时的 Embedding 对齐、跨语言语义匹配）
- Agent 的规划能力（复杂任务如何拆解为多步 Skill 调用）
- 记忆管理的设计（短期 vs 长期、什么时候写入长期记忆、跨平台对话的状态管理）
- Skill 的错误处理和降级（某个 Skill 失败后如何保证系统整体可用）
- 评估体系的设计思路（怎么衡量 Agent 回答的质量、入库质量指标体系）
- 数据隔离与安全（user_id 级别隔离在向量数据库中的实现方式）
