"""
research_state and notifications table helpers.
"""

import json
import logging
import uuid

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _get_db():
    from backend.db.postgres import SyncSession

    return SyncSession()


def save_research_items(user_id: str, items: list[dict], source_session: str = "") -> int:
    if not items:
        return 0

    db = _get_db()
    try:
        count = 0
        for item in items:
            existing = db.execute(
                text(
                    """
                    SELECT id FROM research_state
                    WHERE user_id = :uid AND content = :content AND status = 'open'
                    LIMIT 1
                    """
                ),
                {"uid": user_id, "content": item.get("content", "")},
            ).fetchone()
            if existing:
                continue

            db.execute(
                text(
                    """
                    INSERT INTO research_state
                        (id, user_id, item_type, content, status, source_session, related_docs)
                    VALUES (:id, :uid, :item_type, :content, 'open', :source_session, :related_docs)
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "uid": user_id,
                    "item_type": item.get("type", "question"),
                    "content": item.get("content", ""),
                    "source_session": source_session,
                    "related_docs": json.dumps(item.get("related_docs", []), ensure_ascii=False),
                },
            )
            count += 1

        db.commit()
        return count
    except Exception as exc:
        db.rollback()
        logger.error(f"save_research_items failed: {exc}")
        return 0
    finally:
        db.close()


def get_open_items(user_id: str) -> list[dict]:
    db = _get_db()
    try:
        rows = db.execute(
            text(
                """
                SELECT id, item_type, content, related_docs, created_at, updated_at
                FROM research_state
                WHERE user_id = :uid AND status = 'open'
                ORDER BY updated_at DESC
                LIMIT 30
                """
            ),
            {"uid": user_id},
        ).fetchall()
        return [
            {
                "id": str(r[0]),
                "type": r[1],
                "content": r[2],
                "related_docs": r[3] if isinstance(r[3], list) else json.loads(r[3] or "[]"),
                "created_at": r[4].isoformat() if r[4] else None,
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error(f"get_open_items failed: {exc}")
        return []
    finally:
        db.close()


def get_all_items(user_id: str, limit: int = 50) -> list[dict]:
    db = _get_db()
    try:
        rows = db.execute(
            text(
                """
                SELECT id, item_type, content, status, source_session, related_docs, created_at, updated_at
                FROM research_state
                WHERE user_id = :uid
                ORDER BY updated_at DESC
                LIMIT :lim
                """
            ),
            {"uid": user_id, "lim": limit},
        ).fetchall()
        return [
            {
                "id": str(r[0]),
                "type": r[1],
                "content": r[2],
                "status": r[3],
                "source_session": r[4],
                "related_docs": r[5] if isinstance(r[5], list) else json.loads(r[5] or "[]"),
                "created_at": r[6].isoformat() if r[6] else None,
                "updated_at": r[7].isoformat() if r[7] else None,
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error(f"get_all_items failed: {exc}")
        return []
    finally:
        db.close()


def update_item_status(item_id: str, new_status: str) -> bool:
    db = _get_db()
    try:
        result = db.execute(
            text(
                """
                UPDATE research_state
                SET status = :status, updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"status": new_status, "id": item_id},
        )
        db.commit()
        return result.rowcount > 0
    except Exception as exc:
        db.rollback()
        logger.error(f"update_item_status failed: {exc}")
        return False
    finally:
        db.close()


def create_notification(
    user_id: str,
    notif_type: str,
    title: str,
    detail: str = "",
    related_doc: str | None = None,
) -> bool:
    db = _get_db()
    try:
        db.execute(
            text(
                """
                INSERT INTO notifications
                    (id, user_id, notif_type, title, detail, related_doc)
                VALUES (:id, :uid, :notif_type, :title, :detail, :related_doc)
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "uid": user_id,
                "notif_type": notif_type,
                "title": title,
                "detail": detail,
                "related_doc": related_doc,
            },
        )
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        logger.error(f"create_notification failed: {exc}")
        return False
    finally:
        db.close()


def get_unread_notifications(user_id: str, limit: int = 20) -> list[dict]:
    db = _get_db()
    try:
        rows = db.execute(
            text(
                """
                SELECT id, notif_type, title, detail, related_doc, created_at
                FROM notifications
                WHERE user_id = :uid AND is_read = FALSE
                ORDER BY created_at DESC
                LIMIT :lim
                """
            ),
            {"uid": user_id, "lim": limit},
        ).fetchall()
        return [
            {
                "id": str(r[0]),
                "type": r[1],
                "title": r[2],
                "detail": r[3],
                "related_doc": str(r[4]) if r[4] else None,
                "created_at": r[5].isoformat() if r[5] else None,
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error(f"get_unread_notifications failed: {exc}")
        return []
    finally:
        db.close()


def mark_read(notification_id: str) -> bool:
    db = _get_db()
    try:
        result = db.execute(
            text("UPDATE notifications SET is_read = TRUE WHERE id = :id"),
            {"id": notification_id},
        )
        db.commit()
        return result.rowcount > 0
    except Exception as exc:
        db.rollback()
        logger.error(f"mark_read failed: {exc}")
        return False
    finally:
        db.close()


def cleanup_old_notifications(days: int = 90) -> int:
    db = _get_db()
    try:
        result = db.execute(
            text(
                """
                DELETE FROM notifications
                WHERE is_read = TRUE AND created_at < NOW() - MAKE_INTERVAL(days => :days)
                """
            ),
            {"days": int(days)},
        )
        db.commit()
        return result.rowcount
    except Exception as exc:
        db.rollback()
        logger.error(f"cleanup_old_notifications failed: {exc}")
        return 0
    finally:
        db.close()
