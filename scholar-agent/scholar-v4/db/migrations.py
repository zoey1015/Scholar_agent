"""
数据库迁移 - 新增表

paper_claims:      论文核心观点（上传时异步提取）
paper_relations:   论文间关系（上传时异步构建）
research_state:    用户研究状态（研究完成后写入）
notifications:     主动通知（新论文匹配已有研究状态时生成）
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

MIGRATION_SQL = """
-- ============================================================
-- 论文核心观点
-- ============================================================
CREATE TABLE IF NOT EXISTS paper_claims (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id   UUID NOT NULL,
    claim_type    VARCHAR(20) NOT NULL,
    content       TEXT NOT NULL,
    section       VARCHAR(200) DEFAULT '',
    created_at    TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_claims_doc ON paper_claims(document_id);
CREATE INDEX IF NOT EXISTS idx_claims_type ON paper_claims(claim_type);

-- ============================================================
-- 论文间关系
-- ============================================================
CREATE TABLE IF NOT EXISTS paper_relations (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_a_id      UUID NOT NULL,
    doc_b_id      UUID NOT NULL,
    relation_type VARCHAR(20) NOT NULL,
    summary       TEXT NOT NULL,
    claim_a_id    UUID,
    claim_b_id    UUID,
    confidence    FLOAT DEFAULT 0.0,
    created_at    TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rels_a ON paper_relations(doc_a_id);
CREATE INDEX IF NOT EXISTS idx_rels_b ON paper_relations(doc_b_id);

-- ============================================================
-- 用户研究状态
-- ============================================================
CREATE TABLE IF NOT EXISTS research_state (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       VARCHAR(36) NOT NULL,
    item_type     VARCHAR(20) NOT NULL,
    content       TEXT NOT NULL,
    status        VARCHAR(20) DEFAULT 'open',
    source_session VARCHAR(36) DEFAULT '',
    related_docs  JSONB DEFAULT '[]',
    created_at    TIMESTAMP DEFAULT NOW(),
    updated_at    TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rs_user ON research_state(user_id, status);

-- ============================================================
-- 主动通知
-- ============================================================
CREATE TABLE IF NOT EXISTS notifications (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       VARCHAR(36) NOT NULL,
    notif_type    VARCHAR(30) NOT NULL,
    title         TEXT NOT NULL,
    detail        TEXT DEFAULT '',
    related_doc   UUID,
    is_read       BOOLEAN DEFAULT FALSE,
    created_at    TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, is_read, created_at DESC);
"""


def run_migrations(engine_or_session):
    """
    执行迁移。兼容 engine 或 session。
    每条 CREATE TABLE/INDEX 单独执行，已存在则跳过。
    """
    from backend.db.postgres import SyncSession

    db = SyncSession()
    try:
        statements = [
            s.strip() for s in MIGRATION_SQL.split(";")
            if s.strip() and not s.strip().startswith("--")
        ]
        for stmt in statements:
            try:
                db.execute(text(stmt))
            except Exception as e:
                # 表/索引已存在等情况，跳过
                db.rollback()
                logger.debug(f"Migration statement skipped: {e}")
                continue
        db.commit()
        logger.info(f"Migration complete: executed {len(statements)} statements")
    except Exception as e:
        db.rollback()
        logger.error(f"Migration failed: {e}")
        raise
    finally:
        db.close()
