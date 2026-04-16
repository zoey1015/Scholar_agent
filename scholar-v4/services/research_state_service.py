"""
研究状态服务 - research_state + notifications 表的 CRUD

功能 C 的核心：追踪用户研究进展，主动推荐。

状态流转：
  question:   open → verified / archived
  hypothesis: open → verified / refuted / archived
  conclusion: 直接写入（无变化）
  direction:  open → archived
"""

import json
import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


def _get_db():
    from backend.db.postgres import SyncSession
    return SyncSession()


# ========================
# research_state CRUD
# ========================

def save_research_items(user_id: str, items: list[dict], source_session: str = "") -> int:
    """
    批量保存研究状态条目

    items: [{"type": "question", "content": "...", "related_docs": [...]}]
    """
    if not items:
        return 0

    db = _get_db()
    try:
        count = 0
        for item in items:
            # 去重：如果已有相同 content 且 status=open，跳过
            existing = db.execute(text("""
                SELECT id FROM research_state
                WHERE user_id = :uid AND content = :c AND status = 'open'
                LIMIT 1
            """), {"uid": user_id, "c": item.get("content", "")}).fetchone()

            if existing:
                continue

            related = json.dumps(item.get("related_docs", []), ensure_ascii=False)
            db.execute(text("""
                INSERT INTO research_state
                    (user_id, item_type, content, status, source_session, related_docs)
                VALUES (:uid, :itype, :content, 'open', :sess, :rdocs)
            """), {
                "uid": user_id,
                "itype": item.get("type", "question"),
                "content": item.get("content", ""),
                "sess": source_session,
                "rdocs": related,
            })
            count += 1

        db.commit()
        logger.info(f"Saved {count} research state items for user {user_id}")
        return count
    except Exception as e:
        db.rollback()
        logger.error(f"save_research_items failed: {e}")
        return 0
    finally:
        db.close()


def get_open_items(user_id: str) -> list[dict]:
    """获取用户所有 open 状态的研究条目（Researcher 的 lookup_state 调用）"""
    db = _get_db()
    try:
        rows = db.execute(text("""
            SELECT id, item_type, content, related_docs, created_at, updated_at
            FROM research_state
            WHERE user_id = :uid AND status = 'open'
            ORDER BY updated_at DESC
            LIMIT 30
        """), {"uid": user_id}).fetchall()

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
    except Exception as e:
        logger.error(f"get_open_items failed: {e}")
        return []
    finally:
        db.close()


def get_all_items(user_id: str, limit: int = 50) -> list[dict]:
    """获取用户所有研究条目（研究看板用）"""
    db = _get_db()
    try:
        rows = db.execute(text("""
            SELECT id, item_type, content, status, source_session,
                   related_docs, created_at, updated_at
            FROM research_state
            WHERE user_id = :uid
            ORDER BY updated_at DESC
            LIMIT :lim
        """), {"uid": user_id, "lim": limit}).fetchall()

        return [
            {
                "id": str(r[0]), "type": r[1], "content": r[2],
                "status": r[3], "source_session": r[4],
                "related_docs": r[5] if isinstance(r[5], list) else json.loads(r[5] or "[]"),
                "created_at": r[6].isoformat() if r[6] else None,
                "updated_at": r[7].isoformat() if r[7] else None,
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"get_all_items failed: {e}")
        return []
    finally:
        db.close()


def update_item_status(item_id: str, new_status: str) -> bool:
    """更新研究条目状态"""
    db = _get_db()
    try:
        db.execute(text("""
            UPDATE research_state SET status = :s, updated_at = NOW()
            WHERE id = :id
        """), {"s": new_status, "id": item_id})
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        return False
    finally:
        db.close()


# ========================
# notifications CRUD
# ========================

def create_notification(
    user_id: str, notif_type: str, title: str,
    detail: str = "", related_doc: str = None,
) -> bool:
    db = _get_db()
    try:
        db.execute(text("""
            INSERT INTO notifications (user_id, notif_type, title, detail, related_doc)
            VALUES (:uid, :ntype, :title, :detail, :rdoc)
        """), {
            "uid": user_id, "ntype": notif_type,
            "title": title, "detail": detail,
            "rdoc": related_doc,
        })
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"create_notification failed: {e}")
        return False
    finally:
        db.close()


def get_unread_notifications(user_id: str, limit: int = 20) -> list[dict]:
    db = _get_db()
    try:
        rows = db.execute(text("""
            SELECT id, notif_type, title, detail, related_doc, created_at
            FROM notifications
            WHERE user_id = :uid AND is_read = FALSE
            ORDER BY created_at DESC
            LIMIT :lim
        """), {"uid": user_id, "lim": limit}).fetchall()

        return [
            {
                "id": str(r[0]), "type": r[1], "title": r[2],
                "detail": r[3], "related_doc": str(r[4]) if r[4] else None,
                "created_at": r[5].isoformat() if r[5] else None,
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"get_unread_notifications failed: {e}")
        return []
    finally:
        db.close()


def mark_read(notification_id: str) -> bool:
    db = _get_db()
    try:
        db.execute(text(
            "UPDATE notifications SET is_read = TRUE WHERE id = :id"
        ), {"id": notification_id})
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        return False
    finally:
        db.close()


def cleanup_old_notifications(days: int = 90) -> int:
    """清理已读的旧通知"""
    db = _get_db()
    try:
        r = db.execute(text("""
            DELETE FROM notifications
            WHERE is_read = TRUE AND created_at < NOW() - MAKE_INTERVAL(days => :d)
        """), {"d": int(days)})
        db.commit()
        return r.rowcount
    except Exception as e:
        db.rollback()
        return 0
    finally:
        db.close()
