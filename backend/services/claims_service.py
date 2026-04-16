"""
paper_claims table helpers.
"""

import logging
import uuid

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _get_db():
    from backend.db.postgres import SyncSession

    return SyncSession()


def save_claims(document_id: str, claims: list[dict]) -> int:
    if not claims:
        return 0

    db = _get_db()
    try:
        count = 0
        for claim in claims:
            db.execute(
                text(
                    """
                    INSERT INTO paper_claims (id, document_id, claim_type, content, section)
                    VALUES (:id, :document_id, :claim_type, :content, :section)
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "document_id": document_id,
                    "claim_type": claim.get("type", "conclusion"),
                    "content": claim.get("content", ""),
                    "section": claim.get("section", ""),
                },
            )
            count += 1
        db.commit()
        return count
    except Exception as exc:
        db.rollback()
        logger.error(f"save_claims failed: {exc}")
        return 0
    finally:
        db.close()


def get_claims_by_document(document_id: str) -> list[dict]:
    db = _get_db()
    try:
        rows = db.execute(
            text(
                """
                SELECT id, claim_type, content, section
                FROM paper_claims
                WHERE document_id = :doc_id
                ORDER BY created_at
                """
            ),
            {"doc_id": document_id},
        ).fetchall()
        return [
            {"id": str(r[0]), "type": r[1], "content": r[2], "section": r[3]}
            for r in rows
        ]
    finally:
        db.close()


def get_all_claims_by_type(claim_type: str, exclude_doc: str | None = None) -> list[dict]:
    db = _get_db()
    try:
        if exclude_doc:
            rows = db.execute(
                text(
                    """
                    SELECT id, document_id, content, section
                    FROM paper_claims
                    WHERE claim_type = :ctype AND document_id != :exc
                    ORDER BY created_at DESC
                    LIMIT 200
                    """
                ),
                {"ctype": claim_type, "exc": exclude_doc},
            ).fetchall()
        else:
            rows = db.execute(
                text(
                    """
                    SELECT id, document_id, content, section
                    FROM paper_claims
                    WHERE claim_type = :ctype
                    ORDER BY created_at DESC
                    LIMIT 200
                    """
                ),
                {"ctype": claim_type},
            ).fetchall()

        return [
            {
                "id": str(r[0]),
                "document_id": str(r[1]),
                "content": r[2],
                "section": r[3],
            }
            for r in rows
        ]
    finally:
        db.close()
