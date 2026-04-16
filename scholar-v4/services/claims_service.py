"""
Claims 服务 - paper_claims 表的 CRUD

上传论文时异步提取核心观点，存入此表。
查询时 Researcher 直接读表。
"""

import json
import logging
from typing import Optional
from sqlalchemy import text

logger = logging.getLogger(__name__)


def _get_db():
    from backend.db.postgres import SyncSession
    return SyncSession()


def save_claims(document_id: str, claims: list[dict]) -> int:
    """
    批量保存论文观点

    claims 格式: [{"type": "method", "content": "...", "section": "..."}]
    返回写入条数
    """
    if not claims:
        return 0

    db = _get_db()
    try:
        count = 0
        for c in claims:
            db.execute(text("""
                INSERT INTO paper_claims (document_id, claim_type, content, section)
                VALUES (:doc_id, :ctype, :content, :section)
            """), {
                "doc_id": document_id,
                "ctype": c.get("type", "conclusion"),
                "content": c.get("content", ""),
                "section": c.get("section", ""),
            })
            count += 1
        db.commit()
        logger.info(f"Saved {count} claims for document {document_id}")
        return count
    except Exception as e:
        db.rollback()
        logger.error(f"save_claims failed: {e}")
        return 0
    finally:
        db.close()


def get_claims_by_document(document_id: str) -> list[dict]:
    """获取某篇论文的所有观点"""
    db = _get_db()
    try:
        rows = db.execute(text("""
            SELECT id, claim_type, content, section
            FROM paper_claims WHERE document_id = :doc_id
            ORDER BY created_at
        """), {"doc_id": document_id}).fetchall()

        return [
            {"id": str(r[0]), "type": r[1], "content": r[2], "section": r[3]}
            for r in rows
        ]
    finally:
        db.close()


def get_all_claims_by_type(claim_type: str, exclude_doc: Optional[str] = None) -> list[dict]:
    """获取某类型的所有观点（用于 build_relations 时对比）"""
    db = _get_db()
    try:
        if exclude_doc:
            rows = db.execute(text("""
                SELECT id, document_id, content, section
                FROM paper_claims
                WHERE claim_type = :ctype AND document_id != :exc
                ORDER BY created_at DESC
                LIMIT 200
            """), {"ctype": claim_type, "exc": exclude_doc}).fetchall()
        else:
            rows = db.execute(text("""
                SELECT id, document_id, content, section
                FROM paper_claims WHERE claim_type = :ctype
                ORDER BY created_at DESC LIMIT 200
            """), {"ctype": claim_type}).fetchall()

        return [
            {"id": str(r[0]), "document_id": str(r[1]),
             "content": r[2], "section": r[3]}
            for r in rows
        ]
    finally:
        db.close()


def delete_claims_by_document(document_id: str) -> int:
    """删除某篇论文的所有观点（论文删除时调用）"""
    db = _get_db()
    try:
        r = db.execute(text(
            "DELETE FROM paper_claims WHERE document_id = :doc_id"
        ), {"doc_id": document_id})
        db.commit()
        return r.rowcount
    except Exception as e:
        db.rollback()
        logger.error(f"delete_claims failed: {e}")
        return 0
    finally:
        db.close()
