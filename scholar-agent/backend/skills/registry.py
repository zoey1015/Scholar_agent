"""
Skill 注册中心
"""

import logging
from typing import Optional

from backend.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class SkillRegistry:
    def __init__(self):
        self._skills: dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> None:
        if skill.name in self._skills:
            logger.warning(f"Skill [{skill.name}] already registered, overwriting.")
        self._skills[skill.name] = skill
        logger.info(f"Skill registered: {skill.name} (v{skill.version})")

    def get(self, name: str) -> Optional[BaseSkill]:
        skill = self._skills.get(name)
        if not skill:
            logger.error(f"Skill [{name}] not found in registry.")
        return skill

    def list_skills(self) -> list[dict]:
        return [
            {"name": s.name, "description": s.description, "version": s.version}
            for s in self._skills.values()
        ]

    def get_tool_definitions(self) -> list[dict]:
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


skill_registry = SkillRegistry()


def init_skills() -> SkillRegistry:
    """初始化并注册所有 Skill"""

    # Phase 1 Skills
    from backend.skills.paper_parser import PaperParserSkill
    from backend.skills.embedding import EmbeddingSkill
    from backend.skills.retrieval import RetrievalSkill

    skill_registry.register(PaperParserSkill())
    skill_registry.register(EmbeddingSkill())
    skill_registry.register(RetrievalSkill())

    # Phase 2 Skills
    from backend.skills.conversation_summary import ConversationSummarySkill
    skill_registry.register(ConversationSummarySkill())

    # Phase 3 Skills (uncomment when implemented)
    # from backend.skills.quality_check import QualityCheckSkill
    # from backend.skills.writing_assist import WritingAssistSkill

    logger.info(f"Initialized {skill_registry.count} skills.")
    return skill_registry
