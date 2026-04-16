"""
Non-blocking post-upload analysis hook.
"""

import logging

logger = logging.getLogger(__name__)


def on_document_ready(document_id: str, user_id: str = None):
    try:
        from backend.tasks.analysis_tasks import trigger_analysis_pipeline

        task_id = trigger_analysis_pipeline(document_id, user_id)
        logger.info(f"Analysis pipeline triggered: doc={document_id}, task={task_id}")
        return task_id
    except Exception as exc:
        logger.warning(f"Analysis pipeline trigger failed (non-blocking): {exc}")
        return None
