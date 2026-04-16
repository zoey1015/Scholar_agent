"""
上传管线集成

在现有的论文上传流程中，解析+向量化完成后，
调用此 hook 触发异步分析管线。

用法（在现有的 documents.py 或 celery task 中）：
    from backend.tasks.upload_hook import on_document_ready
    on_document_ready(document_id, user_id)
"""

import logging

logger = logging.getLogger(__name__)


def on_document_ready(document_id: str, user_id: str = None):
    """
    论文解析+向量化完成后调用。

    触发异步管线：
    1. extract_claims  — 提取核心观点
    2. build_relations — 构建论文间关系
    3. match_state     — 匹配用户研究状态，生成通知
    """
    try:
        from backend.tasks.analysis_tasks import trigger_analysis_pipeline
        task_id = trigger_analysis_pipeline(document_id, user_id)
        logger.info(f"Analysis pipeline triggered: doc={document_id}, task={task_id}")
        return task_id
    except Exception as e:
        # 分析管线失败不应阻塞主流程
        logger.warning(f"Analysis pipeline trigger failed (non-blocking): {e}")
        return None
