"""
论文解析 Skill

Phase 1: 英文学术论文解析（GROBID）
后续: 接入 Nougat 做公式解析增强
"""

import logging
from typing import Any

from backend.skills.base import BaseSkill, SkillContext, SkillResult, SkillStatus

logger = logging.getLogger(__name__)


class PaperParserSkill(BaseSkill):
    name = "paper_parser"
    description = "解析英文学术论文 PDF，提取标题、作者、摘要、章节、引用列表等结构化信息"
    version = "1.0.0"

    def __init__(self):
        from backend.config import get_settings
        self.grobid_url = get_settings().grobid_url

    async def execute(self, context: SkillContext) -> SkillResult:
        """
        解析论文 PDF

        context.metadata 需包含:
            - file_path: str  PDF 文件在 MinIO 中的路径
        """
        file_path = context.metadata.get("file_path", "")
        if not file_path:
            return SkillResult(status=SkillStatus.FAILED, message="file_path is required")

        try:
            parsed = await self._parse_with_grobid(file_path)
            return SkillResult(
                status=SkillStatus.SUCCESS,
                data=parsed,
                message=f"Successfully parsed: {parsed.get('title', 'Unknown')}",
            )
        except Exception as e:
            return await self.on_error(context, e)

    async def _parse_with_grobid(self, file_path: str) -> dict:
        """
        调用 GROBID API 解析论文 PDF

        TODO Phase 1: 实现 GROBID 调用
        - 读取 PDF 文件（从 MinIO 下载或本地路径）
        - 调用 GROBID /api/processFulltextDocument
        - 解析 TEI XML 响应，提取结构化字段
        """
        # Placeholder - 实际实现时替换
        logger.info(f"Parsing paper: {file_path}")
        return {
            "title": "",
            "authors": [],
            "abstract": "",
            "sections": [],
            "references": [],
            "raw_text": "",
        }

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "PDF 文件路径（MinIO 路径或本地路径）",
                }
            },
            "required": ["file_path"],
        }

    def get_output_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "authors": {"type": "array", "items": {"type": "string"}},
                "abstract": {"type": "string"},
                "sections": {"type": "array"},
                "references": {"type": "array"},
            },
        }
