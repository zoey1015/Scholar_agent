"""
混合检索 Skill

BM25 关键词检索 + 向量语义检索 + Reranking 重排序
Phase 1: 先实现纯向量检索
Phase 3: 加入 BM25 + Rerank
"""

import logging
from backend.skills.base import BaseSkill, SkillContext, SkillResult, SkillStatus

logger = logging.getLogger(__name__)


class RetrievalSkill(BaseSkill):
    name = "retrieval"
    description = "从知识库中混合检索相关文档片段（BM25 + 向量 + Rerank），支持中英文查询"
    version = "1.0.0"

    async def execute(self, context: SkillContext) -> SkillResult:
        """
        context.query: 检索查询文本
        context.metadata 可选:
            - top_k: int  返回数量，默认 5
            - doc_type: str  筛选文档类型 "all" / "paper" / "patent"
            - language: str  筛选语言 "all" / "en" / "zh"
        """
        query = context.query
        if not query:
            return SkillResult(status=SkillStatus.FAILED, message="query is required")

        top_k = context.metadata.get("top_k", 5)
        doc_type = context.metadata.get("doc_type", "all")

        try:
            # Phase 1: 纯向量检索
            vector_results = await self._vector_search(query, top_k * 2)

            # Phase 3 TODO: BM25 关键词检索
            # bm25_results = await self._bm25_search(query, top_k * 2)

            # Phase 3 TODO: 合并 + Rerank
            # merged = self._merge_results(vector_results, bm25_results)
            # reranked = await self._rerank(query, merged, top_k)

            results = vector_results[:top_k]

            return SkillResult(
                status=SkillStatus.SUCCESS,
                data={"results": results, "total": len(results)},
                message=f"Found {len(results)} relevant chunks.",
            )
        except Exception as e:
            return await self.on_error(context, e)

    async def _vector_search(self, query: str, top_k: int) -> list[dict]:
        """
        向量语义检索

        TODO Phase 1:
        - 将 query 用 bge-m3 编码为向量
        - 在 Milvus 中做 ANN 检索
        - 返回 [{chunk_id, content, score, document_id, section_title}, ...]
        """
        logger.info(f"Vector search: '{query}', top_k={top_k}")
        return []

    async def _bm25_search(self, query: str, top_k: int) -> list[dict]:
        """
        BM25 关键词检索（PostgreSQL Full-text Search）

        TODO Phase 3:
        - 使用 PostgreSQL 的 tsvector + tsquery
        - 中文需要 zhparser 或 jieba 分词
        """
        return []

    async def _rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        """
        Reranking 重排序（bge-reranker-v2-m3）

        TODO Phase 3:
        - 将 query 和每个 candidate 的 content 组成 pair
        - 用 reranker 模型打分
        - 按分数排序返回 top_k
        """
        return candidates[:top_k]

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
