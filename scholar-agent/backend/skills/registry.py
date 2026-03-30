"""
Skill 注册中心

支持动态注册、发现、热插拔。
MCP Server 和 REST API 通过同一个 Registry 获取 Skill 实例。
"""

import logging
from typing import Optional

from backend.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Skill 注册中心"""

    def __init__(self):
        self._skills: dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> None:
        """注册一个 Skill"""
        if skill.name in self._skills:
            logger.warning(f"Skill [{skill.name}] already registered, overwriting.")
        self._skills[skill.name] = skill
        logger.info(f"Skill registered: {skill.name} (v{skill.version})")

    def get(self, name: str) -> Optional[BaseSkill]:
        """按名称获取 Skill 实例"""
        skill = self._skills.get(name)
        if not skill:
            logger.error(f"Skill [{name}] not found in registry.")
        return skill

    def list_skills(self) -> list[dict]:
        """返回所有 Skill 的名称和描述（供 Agent 选择、API 展示）"""
        return [
            {
                "name": s.name,
                "description": s.description,
                "version": s.version,
            }
            for s in self._skills.values()
        ]

    def get_tool_definitions(self) -> list[dict]:
        """
        生成 LLM function calling 的 tools 定义
        用于 Agent 调度器识别可用工具
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": skill.name,
                    "description": skill.description,
                    "parameters": skill.get_input_schema(),
                },
            }
            for skill in self._skills.values()
        ]

    @property
    def count(self) -> int:
        return len(self._skills)


# ========================
# 全局单例
# ========================
skill_registry = SkillRegistry()


def init_skills() -> SkillRegistry:
    """
    初始化并注册所有 Skill

    在应用启动时调用（main.py 的 lifespan 中）。
    后续新增 Skill 只需要在这里 import 并 register。
    """
    # Phase 1 Skills
    from backend.skills.paper_parser import PaperParserSkill
    from backend.skills.embedding import EmbeddingSkill
    from backend.skills.retrieval import RetrievalSkill

    skill_registry.register(PaperParserSkill())
    skill_registry.register(EmbeddingSkill())
    skill_registry.register(RetrievalSkill())

    # Phase 2 Skills (uncomment when implemented)
    # from backend.skills.conversation_summary import ConversationSummarySkill
    # from backend.skills.patent_parser import PatentParserSkill
    # from backend.skills.doc_type_detector import DocTypeDetectorSkill
    # from backend.skills.literature_search import LiteratureSearchSkill
    # skill_registry.register(ConversationSummarySkill())

    # Phase 3 Skills (uncomment when implemented)
    # from backend.skills.quality_check import QualityCheckSkill
    # from backend.skills.writing_assist import WritingAssistSkill
    # from backend.skills.knowledge_graph import KnowledgeGraphSkill

    logger.info(f"Initialized {skill_registry.count} skills.")
    return skill_registry
