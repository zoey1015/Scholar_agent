"""
Celery 异步任务队列配置

处理耗时操作：论文解析、向量化、对话总结等
"""

from celery import Celery
from backend.config import get_settings

settings = get_settings()

celery_app = Celery(
    "scholar_agent",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "backend.tasks.parse_tasks",
        "backend.tasks.embedding_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    # 任务重试默认策略
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # 结果过期时间（24小时）
    result_expires=86400,
)
