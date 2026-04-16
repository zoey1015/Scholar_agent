-- ScholarAgent 数据库初始化脚本
-- 在 Docker Compose 启动 PostgreSQL 时自动执行

-- 启用 UUID 生成函数
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 启用 pgvector（用于后续可能的 PG 内向量检索）
CREATE EXTENSION IF NOT EXISTS "vector";

-- ========================
-- 文档元信息表（论文 + 专利统一存储）
-- ========================
CREATE TABLE IF NOT EXISTS documents (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL,
    doc_type        VARCHAR(20) NOT NULL,
    language        VARCHAR(10) NOT NULL,
    title           TEXT NOT NULL,
    authors         JSONB,
    abstract        TEXT,
    year            INTEGER,
    source          VARCHAR(50),
    external_id     VARCHAR(100),
    tags            TEXT[],
    file_path       VARCHAR(500),
    parsed_data     JSONB,
    parse_status    VARCHAR(20) DEFAULT 'pending',
    quality_score   JSONB,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id);
CREATE INDEX IF NOT EXISTS idx_documents_doc_type ON documents(doc_type);
CREATE INDEX IF NOT EXISTS idx_documents_parse_status ON documents(parse_status);

-- 全文检索索引（BM25 混合检索用，Phase 3）
-- 英文
ALTER TABLE documents ADD COLUMN IF NOT EXISTS tsv_en tsvector
    GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(abstract, ''))
    ) STORED;
CREATE INDEX IF NOT EXISTS idx_documents_tsv_en ON documents USING GIN(tsv_en);

-- ========================
-- 文本分块表（chunk 级别，支持精确溯源）
-- ========================
CREATE TABLE IF NOT EXISTS chunks (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    content         TEXT NOT NULL,
    section_title   VARCHAR(200),
    chunk_type      VARCHAR(30),
    embedding_id    VARCHAR(100),
    token_count     INTEGER,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id);

-- chunk 级别全文检索（Phase 3）
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS tsv tsvector
    GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(content, ''))
    ) STORED;
CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON chunks USING GIN(tsv);

-- ========================
-- 研究笔记表
-- ========================
CREATE TABLE IF NOT EXISTS research_notes (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL,
    title           VARCHAR(200),
    summary         TEXT,
    innovations     JSONB,
    hypotheses      JSONB,
    key_questions   JSONB,
    conclusions     JSONB,
    experiments_todo JSONB,
    source_type     VARCHAR(20),
    source_platform VARCHAR(20),
    source_id       VARCHAR(100),
    cited_doc_ids   UUID[],
    cited_chunk_ids UUID[],
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_research_notes_user_id ON research_notes(user_id);

-- ========================
-- 知识图谱关系表
-- ========================
CREATE TABLE IF NOT EXISTS knowledge_edges (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL,
    source_id       UUID NOT NULL,
    target_id       UUID NOT NULL,
    relation_type   VARCHAR(50),
    weight          FLOAT DEFAULT 1.0,
    metadata        JSONB,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- ========================
-- 对话历史表
-- ========================
CREATE TABLE IF NOT EXISTS conversations (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL,
    source_platform VARCHAR(20),
    messages        JSONB,
    summary_id      UUID REFERENCES research_notes(id),
    created_at      TIMESTAMP DEFAULT NOW()
);

-- ========================
-- 异步任务状态表
-- ========================
CREATE TABLE IF NOT EXISTS async_tasks (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL,
    task_type       VARCHAR(50) NOT NULL,
    status          VARCHAR(20) DEFAULT 'pending',
    input_data      JSONB,
    result_data     JSONB,
    error_message   TEXT,
    retry_count     INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_async_tasks_user_id ON async_tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_async_tasks_status ON async_tasks(status);

-- ========================
-- Agent 执行链路追踪表
-- ========================
CREATE TABLE IF NOT EXISTS agent_traces (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL,
    session_id      UUID NOT NULL,
    step_index      INTEGER,
    node_name       VARCHAR(50),
    skill_name      VARCHAR(50),
    input_data      JSONB,
    output_data     JSONB,
    model_used      VARCHAR(50),
    latency_ms      INTEGER,
    token_usage     JSONB,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_traces_session_id ON agent_traces(session_id);
