"""
Research feature migrations.

Adds tables for extracted paper claims, paper relations, research state,
and notifications.
"""

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS paper_claims (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL,
    claim_type VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    section VARCHAR(200) DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_claims_doc ON paper_claims(document_id);
CREATE INDEX IF NOT EXISTS idx_claims_type ON paper_claims(claim_type);

CREATE TABLE IF NOT EXISTS paper_relations (
    id UUID PRIMARY KEY,
    doc_a_id UUID NOT NULL,
    doc_b_id UUID NOT NULL,
    relation_type VARCHAR(20) NOT NULL,
    summary TEXT NOT NULL,
    claim_a_id UUID,
    claim_b_id UUID,
    confidence FLOAT DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rels_a ON paper_relations(doc_a_id);
CREATE INDEX IF NOT EXISTS idx_rels_b ON paper_relations(doc_b_id);

CREATE TABLE IF NOT EXISTS research_state (
    id UUID PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    item_type VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    status VARCHAR(20) DEFAULT 'open',
    source_session VARCHAR(36) DEFAULT '',
    related_docs JSONB DEFAULT '[]',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rs_user ON research_state(user_id, status);

CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    notif_type VARCHAR(30) NOT NULL,
    title TEXT NOT NULL,
    detail TEXT DEFAULT '',
    related_doc UUID,
    is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, is_read, created_at DESC);
"""


def run_migrations(engine_or_session):
    """Execute idempotent SQL migrations using the sync session."""
    from backend.db.postgres import SyncSession

    db = SyncSession()
    try:
        statements = [
            stmt.strip()
            for stmt in MIGRATION_SQL.split(";")
            if stmt.strip()
        ]
        for stmt in statements:
            try:
                db.execute(text(stmt))
            except Exception as exc:
                db.rollback()
                logger.debug(f"Migration statement skipped: {exc}")
        db.commit()
        logger.info("Research feature migrations complete")
    except Exception as exc:
        db.rollback()
        logger.error(f"Migration failed: {exc}")
        raise
    finally:
        db.close()