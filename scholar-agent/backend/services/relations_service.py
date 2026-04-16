"""
paper_relations table helpers.
"""

import logging
import uuid

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _get_db():
    from backend.db.postgres import SyncSession

    return SyncSession()


def save_relation(
    doc_a_id: str,
    doc_b_id: str,
    relation_type: str,
    summary: str,
    claim_a_id: str | None = None,
    claim_b_id: str | None = None,
    confidence: float = 0.0,
) -> bool:
    db = _get_db()
    try:
        db.execute(
            text(
                """
                INSERT INTO paper_relations
                    (id, doc_a_id, doc_b_id, relation_type, summary, claim_a_id, claim_b_id, confidence)
                VALUES (:id, :doc_a_id, :doc_b_id, :relation_type, :summary, :claim_a_id, :claim_b_id, :confidence)
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "doc_a_id": doc_a_id,
                "doc_b_id": doc_b_id,
                "relation_type": relation_type,
                "summary": summary,
                "claim_a_id": claim_a_id,
                "claim_b_id": claim_b_id,
                "confidence": confidence,
            },
        )
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        logger.error(f"save_relation failed: {exc}")
        return False
    finally:
        db.close()


def get_relations_for_documents(doc_ids: list[str]) -> list[dict]:
    if not doc_ids:
        return []

    db = _get_db()
    try:
        placeholders = ", ".join(f":d{i}" for i in range(len(doc_ids)))
        params = {f"d{i}": doc_id for i, doc_id in enumerate(doc_ids)}
        rows = db.execute(
            text(
                f"""
                SELECT id, doc_a_id, doc_b_id, relation_type, summary, confidence, created_at
                FROM paper_relations
                WHERE doc_a_id IN ({placeholders}) OR doc_b_id IN ({placeholders})
                ORDER BY confidence DESC, created_at DESC
                LIMIT 30
                """
            ),
            params,
        ).fetchall()

        return [
            {
                "id": str(r[0]),
                "doc_a_id": str(r[1]),
                "doc_b_id": str(r[2]),
                "relation_type": r[3],
                "summary": r[4],
                "confidence": r[5],
                "created_at": r[6].isoformat() if r[6] else None,
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error(f"get_relations_for_documents failed: {exc}")
        return []
    finally:
        db.close()


def get_all_relations(limit: int = 50) -> list[dict]:
    db = _get_db()
    try:
        rows = db.execute(
            text(
                """
                SELECT id, doc_a_id, doc_b_id, relation_type, summary, confidence, created_at
                FROM paper_relations
                ORDER BY created_at DESC
                LIMIT :lim
                """
            ),
            {"lim": limit},
        ).fetchall()

        return [
            {
                "id": str(r[0]),
                "doc_a_id": str(r[1]),
                "doc_b_id": str(r[2]),
                "relation_type": r[3],
                "summary": r[4],
                "confidence": r[5],
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error(f"get_all_relations failed: {exc}")
        return []
    finally:
        db.close()
