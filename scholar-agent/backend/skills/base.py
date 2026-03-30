"""
Skill 抽象基类

所有 Skill 继承此基类，实现可插拔、可编排的能力模块。
MCP Server、REST API、代理编排层共享同一套 Skill。
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel


class SkillStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"     # 部分成功，降级处理


class SkillContext(BaseModel):
    """Skill 执行上下文，在 Skill 间传递"""

    user_id: str
    session_id: str = ""
    query: str = ""
    retrieved_docs: list[dict] = []
    conversation_history: list[dict] = []
    metadata: dict[str, Any] = {}

    class Config:
        arbitrary_types_allowed = True


class SkillResult(BaseModel):
    """Skill 执行结果"""

    status: SkillStatus
    data: Any = None
    message: str = ""
    artifacts: list[str] = []                   # 生成的文件路径
    next_skill_hint: Optional[str] = None       # 建议下一步调用的 Skill


class BaseSkill(ABC):
    """
    Skill 抽象基类

    每个 Skill 负责一个明确的职责，例如：
    - PaperParserSkill: 解析论文 PDF
    - RetrievalSkill: 混合检索知识库
    - ConversationSummarySkill: 总结对话

    Skill 之间通过 SkillContext 传递数据，
    由 Agent 调度器（LangGraph）或 MCP Tool Handler 负责编排。
    """

    name: str = "base_skill"
    description: str = "Base skill"
    version: str = "1.0.0"

    @abstractmethod
    async def execute(self, context: SkillContext) -> SkillResult:
        """执行 Skill 的核心逻辑"""
        ...

    @abstractmethod
    def get_input_schema(self) -> dict:
        """返回输入参数的 JSON Schema（供 Agent 和 MCP Tool 使用）"""
        ...

    @abstractmethod
    def get_output_schema(self) -> dict:
        """返回输出数据的 JSON Schema"""
        ...

    def validate_input(self, context: SkillContext) -> bool:
        """输入参数校验，子类可覆写"""
        return True

    async def on_error(self, context: SkillContext, error: Exception) -> SkillResult:
        """
        错误处理与降级策略，子类可覆写

        默认行为：返回 FAILED 状态和错误信息
        子类可以实现降级逻辑，例如：
        - 使用缓存数据
        - 切换到备用模型
        - 返回部分结果
        """
        return SkillResult(
            status=SkillStatus.FAILED,
            message=f"Skill [{self.name}] failed: {str(error)}",
        )
