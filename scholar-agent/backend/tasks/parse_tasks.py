"""
论文/专利解析异步任务
"""

import logging
from backend.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def parse_document_task(self, document_id: str, file_path: str, doc_type: str = "paper"):
    """
    异步解析文档

    流程:
    1. 更新任务状态为 processing
    2. 根据 doc_type 调用对应的 Parser Skill
    3. 将解析结果写入 documents 表
    4. 触发向量化任务
    5. 更新任务状态为 success / failed
    """
    logger.info(f"Parsing document {document_id} ({doc_type}): {file_path}")

    # TODO Phase 1: 实现完整解析流程
    # 1. 从 MinIO 下载文件
    # 2. 调用 PaperParserSkill / PatentParserSkill
    # 3. 更新 documents 表（parsed_data, parse_status）
    # 4. 分块并写入 chunks 表
    # 5. 触发 embed_chunks_task

    logger.info(f"Document {document_id} parsed successfully.")
    return {"document_id": document_id, "status": "success"}
