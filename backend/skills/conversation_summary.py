"""
对话总结 Skill

输入：一段对话内容（用户和 AI 的多轮交流）
输出：结构化研究笔记，包含：
  - 标题
  - 核心问题
  - 创新点
  - 关键假设
  - 主要结论
  - 待验证实验
  - 引用的文献 ID

内部调用配置好的 LLM（默认百炼 qwen-plus），
结果存入 research_notes 表并向量化。
"""

import json
import logging
import re

from backend.skills.base import BaseSkill, SkillContext, SkillResult, SkillStatus

logger = logging.getLogger(__name__)

# 结构化提取的 Prompt
SUMMARY_SYSTEM_PROMPT = """你是一个专业的科研助手，擅长从学术讨论中提炼结构化的研究笔记。

请从用户提供的对话内容中提取关键信息，严格按照以下 JSON 格式输出，不要输出任何其他内容：

{
  "title": "简洁的笔记标题（10-30字）",
  "summary": "核心内容概述（100-200字）",
  "key_questions": ["核心问题1", "核心问题2"],
  "innovations": ["创新点1", "创新点2"],
  "hypotheses": ["关键假设或前提1", "关键假设或前提2"],
  "conclusions": ["主要结论1", "主要结论2"],
  "experiments_todo": ["待验证实验或下一步工作1", "待验证实验或下一步工作2"],
  "keywords": ["关键词1", "关键词2", "关键词3"]
}

要求：
- 所有字段必须存在，如无内容填空列表 []
- 内容要简洁精准，避免冗余
- 使用与对话相同的语言（中文对话输出中文，英文对话输出英文）
- 只输出 JSON，不要有任何前缀或后缀说明
"""


class ConversationSummarySkill(BaseSkill):
    name = "conversation_summary"
    description = "将对话内容总结为结构化研究笔记，提取创新点、假设、结论、待验证实验等"
    version = "1.0.0"

    async def execute(self, context: SkillContext) -> SkillResult:
        """
        总结对话内容为结构化笔记

        context.metadata 需包含:
            - conversation: str   需要总结的对话文本
            - title: str          (可选) 笔记标题，不填则 LLM 自动生成
            - source_platform: str (可选) 来源平台，如 "claude" / "chatgpt"
            - cited_doc_ids: list  (可选) 相关文档 ID 列表
        """
        conversation = context.metadata.get("conversation", "").strip()
        if not conversation:
            return SkillResult(
                status=SkillStatus.FAILED,
                message="conversation is required",
            )

        if len(conversation) < 50:
            return SkillResult(
                status=SkillStatus.FAILED,
                message="conversation too short to summarize (min 50 chars)",
            )

        try:
            # Step 1: 调用 LLM 提炼结构化笔记
            structured = await self._extract_structured_note(conversation)

            # Step 2: 如果用户传了标题，覆盖 LLM 生成的
            user_title = context.metadata.get("title", "").strip()
            if user_title:
                structured["title"] = user_title

            # Step 3: 补充元信息
            source_platform = context.metadata.get("source_platform", "")
            cited_doc_ids = context.metadata.get("cited_doc_ids", [])

            note_data = {
                **structured,
                "source_platform": source_platform,
                "cited_doc_ids": cited_doc_ids,
                "raw_conversation": conversation[:2000],  # 保留前 2000 字供溯源
            }

            logger.info(
                f"Conversation summarized: title='{structured.get('title', '')}', "
                f"innovations={len(structured.get('innovations', []))}, "
                f"conclusions={len(structured.get('conclusions', []))}"
            )

            return SkillResult(
                status=SkillStatus.SUCCESS,
                data=note_data,
                message=f"Successfully summarized: {structured.get('title', 'Untitled')}",
            )

        except Exception as e:
            logger.error(f"Conversation summary failed: {e}", exc_info=True)
            return await self.on_error(context, e)

    async def _extract_structured_note(self, conversation: str) -> dict:
        """调用 LLM 提取结构化笔记"""
        from backend.llm_adapters.base import resolve_adapter
        from backend.config import get_settings

        settings = get_settings()
        model = settings.default_llm_model

        try:
            adapter, model_name = resolve_adapter(model)
        except ValueError:
            # 如果默认模型不可用，尝试其他模型
            for fallback in ["qwen-plus", "deepseek-chat", "gpt-4o-mini"]:
                try:
                    adapter, model_name = resolve_adapter(fallback)
                    break
                except ValueError:
                    continue
            else:
                raise RuntimeError("No available LLM adapter configured")

        # 截断过长的对话（避免超出 context window）
        max_conv_length = 8000
        if len(conversation) > max_conv_length:
            conversation = conversation[:max_conv_length] + "\n\n[对话已截断...]"

        messages = [
            {
                "role": "user",
                "content": f"请总结以下研究对话：\n\n{conversation}",
            }
        ]

        response_text = await adapter.chat(
            model=model_name,
            messages=messages,
            system=SUMMARY_SYSTEM_PROMPT,
            temperature=0.3,    # 低温度保证输出稳定
            max_tokens=1500,
        )

        return self._parse_json_response(response_text)

    def _parse_json_response(self, response_text: str) -> dict:
        """
        解析 LLM 返回的 JSON

        LLM 有时会在 JSON 外面加 markdown 代码块，这里做鲁棒处理。
        """
        text = response_text.strip()

        # 去掉 markdown 代码块
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE)
        text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 尝试找 JSON 对象
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                data = json.loads(match.group())
            else:
                logger.warning(f"Failed to parse LLM JSON response: {text[:200]}")
                # 返回最小可用结构
                data = {
                    "title": "对话笔记",
                    "summary": text[:500],
                    "key_questions": [],
                    "innovations": [],
                    "hypotheses": [],
                    "conclusions": [],
                    "experiments_todo": [],
                    "keywords": [],
                }

        # 确保所有字段存在
        defaults = {
            "title": "未命名笔记",
            "summary": "",
            "key_questions": [],
            "innovations": [],
            "hypotheses": [],
            "conclusions": [],
            "experiments_todo": [],
            "keywords": [],
        }
        for k, v in defaults.items():
            if k not in data:
                data[k] = v

        return data

    async def on_error(self, context: SkillContext, error: Exception) -> SkillResult:
        """降级：LLM 失败时返回纯文本摘要"""
        conversation = context.metadata.get("conversation", "")
        title = context.metadata.get("title", "对话笔记")

        # 最简单的降级：取前 300 字作为摘要
        summary = conversation[:300] + "..." if len(conversation) > 300 else conversation

        return SkillResult(
            status=SkillStatus.PARTIAL,
            data={
                "title": title,
                "summary": summary,
                "key_questions": [],
                "innovations": [],
                "hypotheses": [],
                "conclusions": [],
                "experiments_todo": [],
                "keywords": [],
                "source_platform": context.metadata.get("source_platform", ""),
                "cited_doc_ids": [],
                "raw_conversation": conversation[:2000],
            },
            message=f"LLM summary failed, saved raw text: {str(error)[:100]}",
        )

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "conversation": {
                    "type": "string",
                    "description": "需要总结的对话内容",
                },
                "title": {
                    "type": "string",
                    "description": "笔记标题（可选，不填则自动生成）",
                },
                "source_platform": {
                    "type": "string",
                    "description": "来源平台：claude / chatgpt / gemini 等",
                },
                "cited_doc_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "对话中引用的文档 ID 列表",
                },
            },
            "required": ["conversation"],
        }

    def get_output_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "key_questions": {"type": "array"},
                "innovations": {"type": "array"},
                "hypotheses": {"type": "array"},
                "conclusions": {"type": "array"},
                "experiments_todo": {"type": "array"},
                "keywords": {"type": "array"},
            },
        }
