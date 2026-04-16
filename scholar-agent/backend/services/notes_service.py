"""
研究笔记服务

负责笔记的保存（DB + 向量化）和检索（语义搜索）。
被 Notes API 和 MCP Tool Handler 共同调用。
"""

import logging
import uuid
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy import select, update, delete

from backend.db.postgres import SyncSession
from backend.models.document import ResearchNote

logger = logging.getLogger(__name__)

# Milvus 笔记 collection 名称（与 chunks 分开存储）
NOTES_COLLECTION = "scholar_notes"
NOTE_EMBEDDING_DIM = 1024  # bge-m3


class NotesService:
    """研究笔记的保存和检索服务"""

    def save_note(
        self,
        user_id: str,
        note_data: dict,
        source_type: str = "mcp",
    ) -> str:
        """
        保存研究笔记到 PostgreSQL + Milvus

        Args:
            user_id: 用户 ID
            note_data: ConversationSummarySkill 的输出
            source_type: 来源类型 "mcp" / "api" / "cli"

        Returns:
            note_id: 新创建的笔记 ID
        """
        db = SyncSession()
        try:
            note_id = str(uuid.uuid4())

            note = ResearchNote(
                id=uuid.UUID(note_id),
                user_id=uuid.UUID(user_id),
                title=note_data.get("title", "未命名笔记")[:200],
                summary=note_data.get("summary", ""),
                innovations=note_data.get("innovations", []),
                hypotheses=note_data.get("hypotheses", []),
                key_questions=note_data.get("key_questions", []),
                conclusions=note_data.get("conclusions", []),
                experiments_todo=note_data.get("experiments_todo", []),
                source_type=source_type,
                source_platform=note_data.get("source_platform", ""),
                cited_doc_ids=[
                    uuid.UUID(did) for did in note_data.get("cited_doc_ids", [])
                    if self._is_valid_uuid(did)
                ],
            )

            db.add(note)
            db.commit()

            logger.info(f"Note saved to DB: {note_id} - {note_data.get('title', '')}")

            # 向量化笔记内容，存入 Milvus
            self._embed_note(note_id, user_id, note_data)

            return note_id

        except Exception as e:
            db.rollback()
            logger.error(f"Failed to save note: {e}", exc_info=True)
            raise
        finally:
            db.close()

    def search_notes(
        self,
        user_id: str,
        query: str,
        top_k: int = 5,
    ) -> list[dict]:
        """
        语义检索研究笔记

        同时检索 Milvus（向量语义）和 PostgreSQL（关键词），
        合并去重后返回。
        """
        results = []

        # 1. Milvus 向量检索
        vector_results = self._vector_search_notes(user_id, query, top_k)
        results.extend(vector_results)

        # 2. 如果向量检索结果不足，补充 PostgreSQL 关键词检索
        if len(results) < top_k:
            db = SyncSession()
            try:
                keyword_results = self._keyword_search_notes(db, user_id, query, top_k)
                # 去重（以 note_id 为 key）
                existing_ids = {r["note_id"] for r in results}
                for r in keyword_results:
                    if r["note_id"] not in existing_ids:
                        results.append(r)
            finally:
                db.close()

        return results[:top_k]

    def get_note(self, note_id: str) -> dict | None:
        """获取单条笔记详情"""
        db = SyncSession()
        try:
            stmt = select(ResearchNote).where(
                ResearchNote.id == uuid.UUID(note_id)
            )
            note = db.execute(stmt).scalar_one_or_none()
            if note is None:
                return None
            return self._note_to_dict(note)
        finally:
            db.close()

    def list_notes(
        self,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        """分页获取笔记列表"""
        db = SyncSession()
        try:
            from sqlalchemy import func
            stmt = select(ResearchNote).where(
                ResearchNote.user_id == uuid.UUID(user_id)
            ).order_by(ResearchNote.created_at.desc())

            total = db.execute(
                select(func.count()).select_from(stmt.subquery())
            ).scalar() or 0

            stmt = stmt.offset((page - 1) * page_size).limit(page_size)
            notes = db.execute(stmt).scalars().all()

            return [self._note_to_dict(n) for n in notes], total
        finally:
            db.close()

    def delete_note(self, note_id: str) -> bool:
        """删除笔记（DB + Milvus）"""
        # 删除 Milvus 向量
        try:
            from backend.services.milvus_service import MilvusService
            from pymilvus import Collection, utility
            if utility.has_collection(NOTES_COLLECTION):
                collection = Collection(NOTES_COLLECTION)
                collection.delete(f'note_id == "{note_id}"')
        except Exception as e:
            logger.warning(f"Failed to delete note vector: {e}")

        # 删除 DB 记录
        db = SyncSession()
        try:
            result = db.execute(
                delete(ResearchNote).where(
                    ResearchNote.id == uuid.UUID(note_id)
                )
            )
            db.commit()
            return result.rowcount > 0
        finally:
            db.close()

    # ========================
    # 内部方法
    # ========================

    def _embed_note(self, note_id: str, user_id: str, note_data: dict):
        """将笔记向量化存入 Milvus"""
        try:
            from backend.skills.registry import skill_registry
            import asyncio

            embedding_skill = skill_registry.get("embedding")
            if embedding_skill is None:
                logger.warning("EmbeddingSkill not available, note will not be vectorized")
                return

            # 构建笔记的检索文本（标题 + 摘要 + 关键词）
            text_parts = [
                note_data.get("title", ""),
                note_data.get("summary", ""),
                " ".join(note_data.get("keywords", [])),
                " ".join(note_data.get("innovations", [])),
                " ".join(note_data.get("conclusions", [])),
            ]
            search_text = "\n".join(p for p in text_parts if p)

            if not search_text.strip():
                return

            # 获取向量
            query_vec = embedding_skill.encode_query(search_text)

            # 写入 Milvus notes collection
            self._ensure_notes_collection()
            from pymilvus import Collection
            collection = Collection(NOTES_COLLECTION)

            collection.insert([
                [note_id],
                [user_id],
                [note_data.get("title", "")[:300]],
                [note_data.get("summary", "")[:2000]],
                [query_vec],
            ])
            collection.flush()

            logger.info(f"Note {note_id} vectorized and stored in Milvus")

        except Exception as e:
            logger.warning(f"Note vectorization failed (non-fatal): {e}")

    def _ensure_notes_collection(self):
        """确保 Milvus notes collection 存在"""
        from pymilvus import (
            connections, Collection, CollectionSchema,
            FieldSchema, DataType, utility
        )
        from backend.config import get_settings

        settings = get_settings()
        connections.connect(
            alias="default",
            host=settings.milvus_host,
            port=settings.milvus_port,
        )

        if utility.has_collection(NOTES_COLLECTION):
            collection = Collection(NOTES_COLLECTION)
            collection.load()
            return

        fields = [
            FieldSchema(name="note_id", dtype=DataType.VARCHAR, max_length=36, is_primary=True),
            FieldSchema(name="user_id", dtype=DataType.VARCHAR, max_length=36),
            FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=300),
            FieldSchema(name="summary", dtype=DataType.VARCHAR, max_length=2000),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=NOTE_EMBEDDING_DIM),
        ]

        schema = CollectionSchema(fields=fields, description="Scholar Agent research notes")
        collection = Collection(name=NOTES_COLLECTION, schema=schema)

        collection.create_index(
            field_name="embedding",
            index_params={
                "metric_type": "COSINE",
                "index_type": "IVF_FLAT",
                "params": {"nlist": 64},
            },
        )
        collection.load()
        logger.info(f"Created Milvus collection: {NOTES_COLLECTION}")

    def _vector_search_notes(self, user_id: str, query: str, top_k: int) -> list[dict]:
        """Milvus 向量检索笔记"""
        try:
            from backend.skills.registry import skill_registry
            from pymilvus import Collection, utility

            embedding_skill = skill_registry.get("embedding")
            if embedding_skill is None:
                return []

            self._ensure_notes_collection()

            if not utility.has_collection(NOTES_COLLECTION):
                return []

            query_vec = embedding_skill.encode_query(query)
            collection = Collection(NOTES_COLLECTION)

            results = collection.search(
                data=[query_vec],
                anns_field="embedding",
                param={"metric_type": "COSINE", "params": {"nprobe": 10}},
                limit=top_k,
                expr=f'user_id == "{user_id}"',
                output_fields=["note_id", "title", "summary"],
            )

            formatted = []
            for hits in results:
                for hit in hits:
                    formatted.append({
                        "note_id": hit.entity.get("note_id"),
                        "title": hit.entity.get("title"),
                        "summary": hit.entity.get("summary"),
                        "score": hit.score,
                        "source": "vector",
                    })
            return formatted

        except Exception as e:
            logger.warning(f"Vector search notes failed: {e}")
            return []

    def _keyword_search_notes(
        self, db: Session, user_id: str, query: str, top_k: int
    ) -> list[dict]:
        """PostgreSQL 关键词检索笔记（兜底）"""
        try:
            from sqlalchemy import or_, cast
            from sqlalchemy.dialects.postgresql import JSONB

            stmt = select(ResearchNote).where(
                ResearchNote.user_id == uuid.UUID(user_id),
                or_(
                    ResearchNote.title.ilike(f"%{query}%"),
                    ResearchNote.summary.ilike(f"%{query}%"),
                )
            ).order_by(ResearchNote.created_at.desc()).limit(top_k)

            notes = db.execute(stmt).scalars().all()
            return [
                {
                    "note_id": str(n.id),
                    "title": n.title,
                    "summary": (n.summary or "")[:200],
                    "score": 0.5,
                    "source": "keyword",
                }
                for n in notes
            ]
        except Exception as e:
            logger.warning(f"Keyword search notes failed: {e}")
            return []

    def _note_to_dict(self, note: ResearchNote) -> dict:
        """ORM 对象转 dict"""
        return {
            "note_id": str(note.id),
            "title": note.title,
            "summary": note.summary,
            "innovations": note.innovations or [],
            "hypotheses": note.hypotheses or [],
            "key_questions": note.key_questions or [],
            "conclusions": note.conclusions or [],
            "experiments_todo": note.experiments_todo or [],
            "source_type": note.source_type,
            "source_platform": note.source_platform,
            "cited_doc_ids": [str(d) for d in (note.cited_doc_ids or [])],
            "created_at": note.created_at.isoformat() if note.created_at else None,
        }

    def _is_valid_uuid(self, value: str) -> bool:
        try:
            uuid.UUID(value)
            return True
        except (ValueError, AttributeError):
            return False


# 全局单例
_notes_service = None


def get_notes_service() -> NotesService:
    global _notes_service
    if _notes_service is None:
        _notes_service = NotesService()
    return _notes_service
