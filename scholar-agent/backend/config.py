"""
应用配置管理
使用 pydantic-settings 做类型安全的环境变量读取
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # App
    app_env: str = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "scholar"
    postgres_password: str = "scholar_dev_123"
    postgres_db: str = "scholar_agent"
    database_url: str = "postgresql+asyncpg://scholar:scholar_dev_123@localhost:5432/scholar_agent"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Milvus
    milvus_host: str = "localhost"
    milvus_port: int = 19530

    # MinIO
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "scholar_minio"
    minio_secret_key: str = "scholar_minio_123"
    minio_bucket: str = "scholar-papers"

    # LLM API Keys
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    deepseek_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"
    # 阿里百炼
    dashscope_api_key: str = ""


    # Models
    embedding_model: str = "BAAI/bge-m3"
    reranking_model: str = "BAAI/bge-reranker-v2-m3"
    default_llm_model: str = "claude-sonnet-4-20250514"
    light_llm_model: str = "claude-haiku-4-5-20251001"
    strong_llm_model: str = "claude-sonnet-4-20250514"

    # GROBID
    grobid_url: str = "http://localhost:8070"

    @property
    def sync_database_url(self) -> str:
        """同步数据库 URL（用于 Alembic 迁移等）"""
        return self.database_url.replace("asyncpg", "psycopg2")


@lru_cache()
def get_settings() -> Settings:
    """单例获取配置，避免重复读取 .env"""
    return Settings()
