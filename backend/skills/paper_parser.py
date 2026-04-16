"""
论文解析 Skill（完整实现）

调用 GROBID 解析英文学术论文 PDF → 结构化数据 → 分块。
整合了 GROBID 客户端、MinIO 文件下载、文本分块。
"""

import logging
from typing import Any

from backend.skills.base import BaseSkill, SkillContext, SkillResult, SkillStatus
from backend.services.grobid_client import get_grobid_client
from backend.services.minio_service import get_minio_service
from backend.services.chunker import chunk_document

logger = logging.getLogger(__name__)


class PaperParserSkill(BaseSkill):
    name = "paper_parser"
    description = "解析英文学术论文 PDF，提取标题、作者、摘要、章节、引用列表等结构化信息，并切分为可检索的文本块"
    version = "1.0.0"

    async def execute(self, context: SkillContext) -> SkillResult:
        """
        解析论文 PDF

        context.metadata 需包含:
            - file_path: str      MinIO 中的文件路径
            - document_id: str    文档 ID
            - pdf_bytes: bytes    (可选) 直接传入 PDF 内容，跳过 MinIO 下载
        """
        file_path = context.metadata.get("file_path", "")
        document_id = context.metadata.get("document_id", "")
        pdf_bytes = context.metadata.get("pdf_bytes", None)

        if not file_path and not pdf_bytes:
            return SkillResult(status=SkillStatus.FAILED, message="file_path or pdf_bytes is required")

        try:
            # Step 1: 获取 PDF 内容
            if pdf_bytes is None:
                logger.info(f"Downloading PDF from MinIO: {file_path}")
                minio = get_minio_service()
                pdf_bytes = minio.download_file(file_path)

            logger.info(f"PDF size: {len(pdf_bytes)} bytes")

            # Step 2: 调用 GROBID 解析
            grobid = get_grobid_client()

            # 检查 GROBID 是否可用
            if not await grobid.is_alive():
                return SkillResult(
                    status=SkillStatus.FAILED,
                    message="GROBID service is not available. Please check if GROBID is running.",
                )

            logger.info("Calling GROBID for full text parsing...")
            parsed_data = await grobid.parse_fulltext(pdf_bytes)

            # Step 3: 质量评估
            quality_score = self._evaluate_quality(parsed_data)
            parsed_data["quality_score"] = quality_score

            # Step 4: 分块
            chunks = []
            if document_id:
                chunks = chunk_document(parsed_data, document_id)

            logger.info(
                f"Paper parsed: title='{parsed_data.get('title', '')[:50]}', "
                f"sections={len(parsed_data.get('sections', []))}, "
                f"chunks={len(chunks)}, "
                f"quality={quality_score}"
            )

            return SkillResult(
                status=SkillStatus.SUCCESS,
                data={
                    "parsed_data": parsed_data,
                    "chunks": chunks,
                    "quality_score": quality_score,
                },
                message=f"Successfully parsed: {parsed_data.get('title', 'Unknown')}",
            )

        except Exception as e:
            logger.error(f"Paper parsing failed: {e}", exc_info=True)
            return await self.on_error(context, e)

    def _evaluate_quality(self, parsed_data: dict) -> dict:
        """
        评估解析质量

        返回各字段的完整性评分和总分。
        用于判断是否需要人工审核或换解析器重试。
        """
        checks = {
            "has_title": bool(parsed_data.get("title", "").strip()),
            "has_authors": len(parsed_data.get("authors", [])) > 0,
            "has_abstract": len(parsed_data.get("abstract", "")) > 50,
            "has_sections": len(parsed_data.get("sections", [])) >= 2,
            "has_references": len(parsed_data.get("references", [])) > 0,
            "abstract_length": len(parsed_data.get("abstract", "")),
            "section_count": len(parsed_data.get("sections", [])),
            "reference_count": len(parsed_data.get("references", [])),
            "total_text_length": len(parsed_data.get("raw_text", "")),
        }

        # 总分（满分 5）
        score = sum([
            checks["has_title"],
            checks["has_authors"],
            checks["has_abstract"],
            checks["has_sections"],
            checks["has_references"],
        ])
        checks["overall_score"] = score
        checks["needs_review"] = score < 3

        return checks

    async def on_error(self, context: SkillContext, error: Exception) -> SkillResult:
        """错误处理：GROBID 失败时尝试 PyMuPDF 降级"""
        logger.warning(f"GROBID failed, attempting PyMuPDF fallback: {error}")

        pdf_bytes = context.metadata.get("pdf_bytes")
        file_path = context.metadata.get("file_path", "")
        document_id = context.metadata.get("document_id", "")

        if pdf_bytes is None and file_path:
            try:
                minio = get_minio_service()
                pdf_bytes = minio.download_file(file_path)
            except Exception:
                return SkillResult(
                    status=SkillStatus.FAILED,
                    message=f"GROBID failed and cannot download file for fallback: {str(error)}",
                )

        if pdf_bytes:
            try:
                parsed = self._fallback_pymupdf(pdf_bytes)
                chunks = chunk_document(parsed, document_id) if document_id else []
                quality = self._evaluate_quality(parsed)
                parsed["quality_score"] = quality

                return SkillResult(
                    status=SkillStatus.PARTIAL,
                    data={
                        "parsed_data": parsed,
                        "chunks": chunks,
                        "quality_score": quality,
                    },
                    message=f"Parsed with PyMuPDF fallback (reduced quality): {parsed.get('title', 'Unknown')}",
                )
            except Exception as fallback_error:
                logger.error(f"PyMuPDF fallback also failed: {fallback_error}")

        return SkillResult(
            status=SkillStatus.FAILED,
            message=f"Paper parsing failed: {str(error)}",
        )

    def _fallback_pymupdf(self, pdf_bytes: bytes) -> dict:
        """
        PyMuPDF 降级解析

        当 GROBID 不可用时，用 PyMuPDF 提取纯文本。
        没有结构化信息（章节、作者等），但至少能拿到文本做检索。
        """
        import fitz  # PyMuPDF

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        pages_text = []
        for page in doc:
            pages_text.append(page.get_text())

        full_text = "\n\n".join(pages_text)
        doc.close()

        # 尝试从前几行提取标题（通常是第一行非空文本）
        lines = [line.strip() for line in full_text.split("\n") if line.strip()]
        title = lines[0] if lines else "Untitled"

        # 简单的章节切分（基于常见章节标题模式）
        sections = self._naive_section_split(full_text)

        return {
            "title": title[:200],
            "authors": [],
            "abstract": "",
            "sections": sections,
            "references": [],
            "keywords": [],
            "raw_text": full_text,
        }

    def _naive_section_split(self, text: str) -> list[dict]:
        """
        简单的基于正则的章节切分（兜底方案）

        匹配常见的章节标题格式：
        - "1. Introduction"
        - "2 Related Work"
        - "III. Method"
        - "ABSTRACT"
        """
        import re

        # 常见章节标题模式
        section_pattern = re.compile(
            r'^(?:'
            r'(?:\d+\.?\s+)'           # "1." or "1 "
            r'|(?:[IVX]+\.?\s+)'       # "III." Roman numerals
            r'|(?:ABSTRACT|INTRODUCTION|RELATED\s+WORK|METHOD|RESULTS|'
            r'DISCUSSION|CONCLUSION|REFERENCES|ACKNOWLEDGMENT)'
            r')'
            r'[A-Z]',
            re.MULTILINE
        )

        sections = []
        matches = list(section_pattern.finditer(text))

        if not matches:
            # 没有匹配到章节标题，整体作为一个 section
            return [{"title": "Full Text", "content": text, "level": 1}]

        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)

            chunk_text = text[start:end].strip()
            # 第一行作为标题
            first_line_end = chunk_text.find("\n")
            if first_line_end > 0:
                title = chunk_text[:first_line_end].strip()
                content = chunk_text[first_line_end:].strip()
            else:
                title = chunk_text[:100]
                content = chunk_text

            sections.append({
                "title": title,
                "content": content,
                "level": 1,
            })

        return sections

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "PDF 文件在 MinIO 中的路径",
                },
                "document_id": {
                    "type": "string",
                    "description": "文档 ID（用于生成 chunk 关联）",
                },
            },
            "required": ["file_path"],
        }

    def get_output_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "parsed_data": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "authors": {"type": "array"},
                        "abstract": {"type": "string"},
                        "sections": {"type": "array"},
                        "references": {"type": "array"},
                    },
                },
                "chunks": {"type": "array"},
                "quality_score": {"type": "object"},
            },
        }
