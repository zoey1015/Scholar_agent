"""
文本向量化 Skill（完整实现）

bge-m3 多语言 Embedding → 写入 Milvus 向量数据库
"""

import logging
from backend.skills.base import BaseSkill, SkillContext, SkillResult, SkillStatus
from backend.services.milvus_service import get_milvus_service

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
            import os

            # 容器内挂载路径
            local_model_path = "/app/modelscope_cache/hub/BAAI/bge-m3"

            if os.path.exists(local_model_path):
                model_path = local_model_path
                logger.info(f"Using local model: {model_path}")
            else:
                from backend.config import get_settings
                model_path = get_settings().embedding_model
                logger.warning(f"Local model not found, trying remote: {model_path}")

            self._model = SentenceTransformer(
                model_path,
                local_files_only=os.path.exists(local_model_path),
            )
            logger.info("Embedding model loaded.")

    async def execute(self, context: SkillContext) -> SkillResult:
        """
        对文本块做向量化并写入 Milvus

        context.metadata 需包含:
            - chunks: list[dict]  每个 dict 包含:
                - chunk_id: str
                - document_id: str
                - content: str
                - section_title: str (可选)
                - chunk_type: str (可选)
        """
        chunks = context.metadata.get("chunks", [])
        if not chunks:
            return SkillResult(status=SkillStatus.FAILED, message="chunks is required")

        try:
            # Step 1: 加载模型并编码
            self._load_model()
            texts = [c["content"] for c in chunks]

            logger.info(f"Encoding {len(texts)} chunks...")
            embeddings = self._model.encode(
                texts,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            embeddings_list = embeddings.tolist()

            # Step 2: 写入 Milvus
            milvus = get_milvus_service()
            count = milvus.insert_embeddings(
                chunk_ids=[c["chunk_id"] for c in chunks],
                document_ids=[c["document_id"] for c in chunks],
                user_ids=[context.user_id] * len(chunks),
                contents=[c["content"] for c in chunks],
                section_titles=[c.get("section_title", "") for c in chunks],
                chunk_types=[c.get("chunk_type", "section") for c in chunks],
                embeddings=embeddings_list,
            )

            logger.info(f"Embedded and stored {count} chunks in Milvus")

            return SkillResult(
                status=SkillStatus.SUCCESS,
                data={
                    "embedding_ids": [c["chunk_id"] for c in chunks],
                    "count": count,
                    "dimension": len(embeddings_list[0]) if embeddings_list else 0,
                },
                message=f"Embedded {count} chunks.",
            )

        except Exception as e:
            logger.error(f"Embedding failed: {e}", exc_info=True)
            return await self.on_error(context, e)

    def encode_query(self, query: str) -> list[float]:
        """
        编码查询文本为向量

        供 RetrievalSkill 调用，不走 Milvus 写入。
        """
        self._load_model()
        embedding = self._model.encode(
            [query],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embedding[0].tolist()

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "chunks": {
                    "type": "array",
                    "description": "文本块列表，每个包含 chunk_id, document_id, content",
                },
            },
            "required": ["chunks"],
        }

    def get_output_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "embedding_ids": {"type": "array", "items": {"type": "string"}},
                "count": {"type": "integer"},
                "dimension": {"type": "integer"},
            },
        }
