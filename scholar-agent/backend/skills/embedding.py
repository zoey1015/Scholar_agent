"""
文本向量化 Skill（bge-m3 多语言模型）
"""

import logging
from backend.skills.base import BaseSkill, SkillContext, SkillResult, SkillStatus

logger = logging.getLogger(__name__)


class EmbeddingSkill(BaseSkill):
    name = "embedding"
    description = "将文本块向量化（bge-m3 多语言模型），写入 Milvus 向量数据库"
    version = "1.0.0"

    def __init__(self):
        self._model = None  # lazy load, 避免启动时加载大模型

    def _load_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            from backend.config import get_settings
            logger.info("Loading embedding model...")
            self._model = SentenceTransformer(get_settings().embedding_model)
            logger.info("Embedding model loaded.")

    async def execute(self, context: SkillContext) -> SkillResult:
        """
        context.metadata 需包含:
            - chunks: list[dict]  每个 dict 包含 {"content": str, "chunk_id": str}
            - collection_name: str  Milvus collection 名称
        """
        chunks = context.metadata.get("chunks", [])
        if not chunks:
            return SkillResult(status=SkillStatus.FAILED, message="chunks is required")

        try:
            self._load_model()
            texts = [c["content"] for c in chunks]
            embeddings = self._model.encode(texts, normalize_embeddings=True)

            # TODO: 写入 Milvus
            embedding_ids = [c.get("chunk_id", str(i)) for i, c in enumerate(chunks)]

            return SkillResult(
                status=SkillStatus.SUCCESS,
                data={"embedding_ids": embedding_ids, "count": len(embeddings)},
                message=f"Embedded {len(embeddings)} chunks.",
            )
        except Exception as e:
            return await self.on_error(context, e)

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "chunks": {"type": "array", "description": "文本块列表"},
                "collection_name": {"type": "string"},
            },
            "required": ["chunks"],
        }

    def get_output_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "embedding_ids": {"type": "array", "items": {"type": "string"}},
                "count": {"type": "integer"},
            },
        }
