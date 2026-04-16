"""
Relations 服务 - paper_relations 表的 CRUD

上传论文时异步构建论文间关系，存入此表。
查询时 Researcher 直接读表（0.1 秒）。
"""

import logging
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
    claim_a_id: str = None,
    claim_b_id: str = None,
    confidence: float = 0.0,
) -> bool:
    """保存一条论文间关系"""
    db = _get_db()
    try:
        db.execute(text("""
            INSERT INTO paper_relations
                (doc_a_id, doc_b_id, relation_type, summary, claim_a_id, claim_b_id, confidence)
            VALUES (:a, :b, :rtype, :summary, :ca, :cb, :conf)
        """), {
            "a": doc_a_id, "b": doc_b_id,
            "rtype": relation_type, "summary": summary,
            "ca": claim_a_id, "cb": claim_b_id,
            "conf": confidence,
        })
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"save_relation failed: {e}")
        return False
    finally:
        db.close()


def get_relations_for_documents(doc_ids: list[str]) -> list[dict]:
    """
    查询涉及指定文档的所有关系

    Researcher 的 lookup_relations 工具调用此函数。
    返回结果已经是预计算好的，查询耗时 ~0.1 秒。
    """
    if not doc_ids:
        return []

    db = _get_db()
    try:
        # 构建 IN 查询（用参数化防注入）
        placeholders = ", ".join(f":d{i}" for i in range(len(doc_ids)))
        params = {f"d{i}": did for i, did in enumerate(doc_ids)}

        rows = db.execute(text(f"""
            SELECT id, doc_a_id, doc_b_id, relation_type, summary, confidence, created_at
            FROM paper_relations
            WHERE doc_a_id IN ({placeholders}) OR doc_b_id IN ({placeholders})
            ORDER BY confidence DESC, created_at DESC
            LIMIT 30
        """), params).fetchall()

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
    except Exception as e:
        logger.error(f"get_relations_for_documents failed: {e}")
        return []
    finally:
        db.close()


def get_all_relations(limit: int = 50) -> list[dict]:
    """获取所有关系（研究看板用）"""
    db = _get_db()
    try:
        rows = db.execute(text("""
            SELECT r.id, r.doc_a_id, r.doc_b_id, r.relation_type,
                   r.summary, r.confidence, r.created_at
            FROM paper_relations r
            ORDER BY r.created_at DESC
            LIMIT :lim
        """), {"lim": limit}).fetchall()

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
    except Exception as e:
        logger.error(f"get_all_relations failed: {e}")
        return []
    finally:
        db.close()


def delete_relations_by_document(doc_id: str) -> int:
    """删除涉及某文档的所有关系"""
    db = _get_db()
    try:
        r = db.execute(text(
            "DELETE FROM paper_relations WHERE doc_a_id = :d OR doc_b_id = :d"
        ), {"d": doc_id})
        db.commit()
        return r.rowcount
    except Exception as e:
        db.rollback()
        return 0
    finally:
        db.close()
