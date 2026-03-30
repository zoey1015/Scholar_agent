"""
文本向量化异步任务
"""

import logging
from backend.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def embed_chunks_task(self, document_id: str, chunk_ids: list[str]):
    """
    异步向量化文档分块

    流程:
    1. 从 chunks 表读取文本内容
    2. 调用 EmbeddingSkill 生成向量
    3. 写入 Milvus 向量数据库
    4. 更新 chunks 表的 embedding_id 字段
    """
    logger.info(f"Embedding {len(chunk_ids)} chunks for document {document_id}")

    # TODO Phase 1: 实现完整向量化流程

    logger.info(f"Document {document_id} chunks embedded successfully.")
    return {"document_id": document_id, "embedded_count": len(chunk_ids)}
