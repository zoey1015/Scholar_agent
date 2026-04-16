"""
Milvus 向量数据库服务

管理 collection 创建、向量写入、ANN 检索。
"""

import logging
from typing import Optional

from pymilvus import (
    connections,
    Collection,
    CollectionSchema,
    FieldSchema,
    DataType,
    utility,
)

from backend.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# Collection 名称
CHUNKS_COLLECTION = "scholar_chunks"

# 向量维度（bge-m3 输出 1024 维）
EMBEDDING_DIM = 1024


class MilvusService:
    """Milvus 向量数据库操作封装"""

    def __init__(self):
        self._connected = False

    def connect(self):
        """连接 Milvus"""
        if self._connected:
            return
        try:
            connections.connect(
                alias="default",
                host=settings.milvus_host,
                port=settings.milvus_port,
            )
            self._connected = True
            logger.info(f"Connected to Milvus at {settings.milvus_host}:{settings.milvus_port}")
        except Exception as e:
            logger.error(f"Milvus connection failed: {e}")
            raise

    def ensure_collection(self) -> Collection:
        """
        确保 chunks collection 存在，不存在则创建。
        返回 Collection 对象。
        """
        self.connect()

        if utility.has_collection(CHUNKS_COLLECTION):
            collection = Collection(CHUNKS_COLLECTION)
            collection.load()
            return collection

        # 定义 Schema
        fields = [
            FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=36, is_primary=True),
            FieldSchema(name="document_id", dtype=DataType.VARCHAR, max_length=36),
            FieldSchema(name="user_id", dtype=DataType.VARCHAR, max_length=36),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=8000),
            FieldSchema(name="section_title", dtype=DataType.VARCHAR, max_length=300),
            FieldSchema(name="chunk_type", dtype=DataType.VARCHAR, max_length=30),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
        ]

        schema = CollectionSchema(
            fields=fields,
            description="Scholar Agent document chunks with embeddings",
        )

        collection = Collection(
            name=CHUNKS_COLLECTION,
            schema=schema,
        )

        # 创建 IVF_FLAT 索引（适合中小规模数据）
        index_params = {
            "metric_type": "COSINE",
            "index_type": "IVF_FLAT",
            "params": {"nlist": 128},
        }
        collection.create_index(field_name="embedding", index_params=index_params)
        collection.load()

        logger.info(f"Created Milvus collection: {CHUNKS_COLLECTION}")
        return collection

    def insert_embeddings(
        self,
        chunk_ids: list[str],
        document_ids: list[str],
        user_ids: list[str],
        contents: list[str],
        section_titles: list[str],
        chunk_types: list[str],
        embeddings: list[list[float]],
    ) -> int:
        """
        批量写入向量

        Returns:
            写入数量
        """
        collection = self.ensure_collection()

        # Milvus VARCHAR 有长度限制，截断超长内容
        contents_truncated = [c[:7900] for c in contents]
        section_titles_truncated = [s[:290] for s in section_titles]

        data = [
            chunk_ids,
            document_ids,
            user_ids,
            contents_truncated,
            section_titles_truncated,
            chunk_types,
            embeddings,
        ]

        result = collection.insert(data)
        collection.flush()

        logger.info(f"Inserted {len(chunk_ids)} embeddings into Milvus")
        return len(chunk_ids)

    def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        user_id: Optional[str] = None,
        doc_type: Optional[str] = None,
    ) -> list[dict]:
        """
        ANN 向量检索

        Args:
            query_embedding: 查询向量
            top_k: 返回数量
            user_id: 用户 ID 过滤
            doc_type: 文档类型过滤

        Returns:
            [{"chunk_id", "document_id", "content", "section_title", "score"}, ...]
        """
        collection = self.ensure_collection()

        search_params = {
            "metric_type": "COSINE",
            "params": {"nprobe": 16},
        }

        # 构建过滤条件
        filter_expr = ""
        filters = []
        if user_id:
            filters.append(f'user_id == "{user_id}"')
        if doc_type and doc_type != "all":
            filters.append(f'chunk_type == "{doc_type}"')
        if filters:
            filter_expr = " and ".join(filters)

        results = collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=top_k,
            expr=filter_expr if filter_expr else None,
            output_fields=["chunk_id", "document_id", "content", "section_title", "chunk_type"],
        )

        # 格式化结果
        formatted = []
        for hits in results:
            for hit in hits:
                formatted.append({
                    "chunk_id": hit.entity.get("chunk_id"),
                    "document_id": hit.entity.get("document_id"),
                    "content": hit.entity.get("content"),
                    "section_title": hit.entity.get("section_title"),
                    "chunk_type": hit.entity.get("chunk_type"),
                    "score": hit.score,
                })

        return formatted

    def delete_by_document(self, document_id: str) -> bool:
        """删除指定文档的所有向量"""
        try:
            collection = self.ensure_collection()
            collection.delete(f'document_id == "{document_id}"')
            logger.info(f"Deleted embeddings for document: {document_id}")
            return True
        except Exception as e:
            logger.error(f"Delete embeddings failed: {e}")
            return False


# 全局单例
_milvus_service: Optional[MilvusService] = None


def get_milvus_service() -> MilvusService:
    global _milvus_service
    if _milvus_service is None:
        _milvus_service = MilvusService()
    return _milvus_service
