"""
混合检索 Skill（Phase 1: 向量检索实现）

Phase 1: 向量语义检索（Milvus ANN）
Phase 3: + BM25 关键词检索 + Rerank 重排序
"""

import logging
from backend.skills.base import BaseSkill, SkillContext, SkillResult, SkillStatus
from backend.skills.registry import skill_registry
from backend.services.milvus_service import get_milvus_service

logger = logging.getLogger(__name__)


class RetrievalSkill(BaseSkill):
    name = "retrieval"
    description = "从知识库中检索相关文档片段（向量语义检索），支持中英文查询"
    version = "1.0.0"

    async def execute(self, context: SkillContext) -> SkillResult:
        """
        context.query: 检索查询文本
        context.metadata 可选:
            - top_k: int  返回数量，默认 5
            - doc_type: str  筛选文档类型 "all" / "paper" / "patent"
        """
        query = context.query
        if not query:
            return SkillResult(status=SkillStatus.FAILED, message="query is required")

        top_k = context.metadata.get("top_k", 5)
        doc_type = context.metadata.get("doc_type", "all")

        try:
            # Step 1: 将查询文本编码为向量
            embedding_skill = skill_registry.get("embedding")
            if embedding_skill is None:
                return SkillResult(
                    status=SkillStatus.FAILED,
                    message="EmbeddingSkill not available, cannot encode query.",
                )

            query_embedding = embedding_skill.encode_query(query)

            # Step 2: Milvus 向量检索
            milvus = get_milvus_service()
            results = milvus.search(
                query_embedding=query_embedding,
                top_k=top_k,
                user_id=context.user_id,
            )

            logger.info(f"Retrieved {len(results)} results for query: '{query[:50]}...'")

            return SkillResult(
                status=SkillStatus.SUCCESS,
                data={"results": results, "total": len(results)},
                message=f"Found {len(results)} relevant chunks.",
            )

        except Exception as e:
            logger.error(f"Retrieval failed: {e}", exc_info=True)
            return await self.on_error(context, e)

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索查询文本"},
                "top_k": {"type": "integer", "default": 5},
                "doc_type": {"type": "string", "enum": ["all", "paper", "patent"], "default": "all"},
            },
            "required": ["query"],
        }

    def get_output_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "results": {"type": "array"},
                "total": {"type": "integer"},
            },
        }
