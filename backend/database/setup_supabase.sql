-- ═══════════════════════════════════════════════════════════════════════════
-- Criterion QA v3 — Complete Supabase Schema
-- ═══════════════════════════════════════════════════════════════════════════
-- HOW TO RUN:
--   1. Go to https://supabase.com → your project → SQL Editor
--   2. Click "New Query"
--   3. Paste this entire file
--   4. Click "Run" (green button)
--
-- This creates ALL tables, indexes, and helper functions.
-- Safe to run multiple times (uses IF NOT EXISTS).
-- ═══════════════════════════════════════════════════════════════════════════

-- ── Enable required extensions ───────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";          -- pgvector for RAG embeddings

-- ── AGENTS ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agents (
    agent_id      TEXT PRIMARY KEY,
    name          TEXT    DEFAULT '',
    team          TEXT    DEFAULT 'General',
    email         TEXT    DEFAULT '',
    total_calls   INTEGER DEFAULT 0,
    avg_score     FLOAT   DEFAULT 0,
    last_call_at  TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- ── CALLS ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS calls (
    call_id          TEXT PRIMARY KEY,
    agent_id         TEXT REFERENCES agents(agent_id) ON DELETE SET NULL,
    customer_id      TEXT    DEFAULT '',
    call_date        TEXT    DEFAULT '',
    filename         TEXT    DEFAULT '',
    file_type        TEXT    DEFAULT 'audio',
    duration_seconds FLOAT   DEFAULT 0,
    word_count       INTEGER DEFAULT 0,
    chunk_count      INTEGER DEFAULT 1,
    status           TEXT    DEFAULT 'pending',
    processing_time  FLOAT   DEFAULT 0,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    processed_at     TIMESTAMPTZ
);

-- ── QUALITY SCORES ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS quality_scores (
    call_id              TEXT PRIMARY KEY REFERENCES calls(call_id) ON DELETE CASCADE,
    overall_rating       FLOAT DEFAULT 0,
    rating_label         TEXT  DEFAULT 'Average',
    empathy              FLOAT DEFAULT 0,
    resolution           FLOAT DEFAULT 0,
    communication        FLOAT DEFAULT 0,
    professionalism      FLOAT DEFAULT 0,
    product_knowledge    FLOAT DEFAULT 0,
    listening            FLOAT DEFAULT 0,
    response_time        FLOAT DEFAULT 0,
    customer_satisfaction FLOAT DEFAULT 0,
    first_call_resolution FLOAT DEFAULT 0,
    compliance           FLOAT DEFAULT 0,
    f1_score             FLOAT DEFAULT 0,
    precision_score      FLOAT DEFAULT 0,
    recall_score         FLOAT DEFAULT 0,
    summary              TEXT  DEFAULT '',
    metrics_json         JSONB DEFAULT '{}',
    feedback_json        JSONB DEFAULT '{}',
    provider             TEXT  DEFAULT '',
    created_at           TIMESTAMPTZ DEFAULT NOW()
);

-- ── SENTIMENT ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sentiment_analysis (
    call_id              TEXT PRIMARY KEY REFERENCES calls(call_id) ON DELETE CASCADE,
    overall_sentiment    TEXT  DEFAULT 'neutral',
    agent_sentiment      TEXT  DEFAULT 'neutral',
    customer_sentiment   TEXT  DEFAULT 'neutral',
    avg_agent_score      FLOAT DEFAULT 0,
    avg_customer_score   FLOAT DEFAULT 0,
    escalation_detected  BOOLEAN DEFAULT FALSE,
    escalation_point     FLOAT,
    timeline_json        JSONB DEFAULT '[]',
    created_at           TIMESTAMPTZ DEFAULT NOW()
);

-- ── TRANSCRIPTS ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS transcripts (
    call_id      TEXT PRIMARY KEY REFERENCES calls(call_id) ON DELETE CASCADE,
    full_text    TEXT  DEFAULT '',
    segments_json JSONB DEFAULT '[]',
    formatted    TEXT  DEFAULT '',
    word_count   INTEGER DEFAULT 0,
    duration     FLOAT   DEFAULT 0,
    speakers     INTEGER DEFAULT 0,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- ── ALERTS ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alerts (
    alert_id     TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    call_id      TEXT REFERENCES calls(call_id) ON DELETE SET NULL,
    agent_id     TEXT,
    alert_type   TEXT NOT NULL,
    severity     TEXT DEFAULT 'medium',    -- critical | high | medium | low
    channel      TEXT DEFAULT 'agent',     -- agent | system
    title        TEXT DEFAULT '',
    message      TEXT DEFAULT '',
    details_json JSONB DEFAULT '{}',
    status       TEXT DEFAULT 'active',    -- active | dismissed
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    dismissed_at TIMESTAMPTZ
);

-- ── UNPARLIAMENTARY HITS ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS unparliamentary_hits (
    id         BIGSERIAL PRIMARY KEY,
    call_id    TEXT REFERENCES calls(call_id) ON DELETE CASCADE,
    agent_id   TEXT,
    word       TEXT,
    speaker    TEXT,
    timestamp  FLOAT,
    context    TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── POLICY DOCUMENTS ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS policy_documents (
    doc_id     TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    title      TEXT NOT NULL,
    content    TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── POLICY CHUNKS (for RAG) ───────────────────────────────────────────────────
-- Each document is split into chunks; embeddings stored as pgvector or JSON
CREATE TABLE IF NOT EXISTS policy_chunks (
    chunk_id   BIGSERIAL PRIMARY KEY,
    doc_id     TEXT REFERENCES policy_documents(doc_id) ON DELETE CASCADE,
    title      TEXT DEFAULT '',
    text       TEXT NOT NULL,
    chunk_idx  INTEGER DEFAULT 0,
    -- pgvector column for semantic search (384-dim for all-MiniLM-L6-v2)
    -- Falls back to TF-IDF if pgvector extension not available
    embedding  vector(384),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── POLICY VIOLATIONS ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS policy_violations (
    id         BIGSERIAL PRIMARY KEY,
    call_id    TEXT REFERENCES calls(call_id) ON DELETE CASCADE,
    doc_id     TEXT,
    rule_text  TEXT DEFAULT '',
    violation  TEXT DEFAULT '',
    severity   TEXT DEFAULT 'medium',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ═══════════════════════════════════════════════════════════════════════════
--  INDEXES  (critical for performance with thousands of calls)
-- ═══════════════════════════════════════════════════════════════════════════
CREATE INDEX IF NOT EXISTS idx_calls_agent        ON calls(agent_id);
CREATE INDEX IF NOT EXISTS idx_calls_date         ON calls(call_date DESC);
CREATE INDEX IF NOT EXISTS idx_calls_status       ON calls(status);
CREATE INDEX IF NOT EXISTS idx_calls_created      ON calls(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_agent       ON alerts(agent_id);
CREATE INDEX IF NOT EXISTS idx_alerts_status      ON alerts(status);
CREATE INDEX IF NOT EXISTS idx_alerts_type        ON alerts(alert_type);
CREATE INDEX IF NOT EXISTS idx_alerts_created     ON alerts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_unparl_call        ON unparliamentary_hits(call_id);
CREATE INDEX IF NOT EXISTS idx_policy_chunks_doc  ON policy_chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_violations_call    ON policy_violations(call_id);
CREATE INDEX IF NOT EXISTS idx_qs_rating          ON quality_scores(overall_rating);

-- pgvector index for fast semantic similarity search
-- Uses HNSW algorithm (faster than IVFFlat for small-medium datasets)
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON policy_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ═══════════════════════════════════════════════════════════════════════════
--  HELPER VIEWS  (used by get_all_agents)
-- ═══════════════════════════════════════════════════════════════════════════

-- Agent summary view — replaces the missing get_agents_with_stats RPC
CREATE OR REPLACE VIEW agent_stats_view AS
SELECT
    a.agent_id,
    a.name,
    a.team,
    a.email,
    a.total_calls,
    a.avg_score,
    a.last_call_at,
    a.created_at,
    COALESCE(
        (SELECT ROUND(AVG(qs.overall_rating)::NUMERIC, 2)::FLOAT
         FROM calls c
         JOIN quality_scores qs ON qs.call_id = c.call_id
         WHERE c.agent_id = a.agent_id),
        0
    ) AS computed_avg,
    COALESCE(
        (SELECT COUNT(*)::INTEGER
         FROM calls c
         WHERE c.agent_id = a.agent_id),
        0
    ) AS call_count,
    COALESCE(
        (SELECT COUNT(*)::INTEGER
         FROM alerts al
         WHERE al.agent_id = a.agent_id AND al.status = 'active'),
        0
    ) AS active_alerts
FROM agents a;

-- Alert summary view
CREATE OR REPLACE VIEW alert_summary_view AS
SELECT
    COUNT(*)::INTEGER                                           AS total_active,
    COUNT(*) FILTER (WHERE severity = 'critical')::INTEGER     AS critical_count,
    COUNT(*) FILTER (WHERE severity = 'high')::INTEGER         AS high_count,
    COUNT(*) FILTER (WHERE severity = 'medium')::INTEGER       AS medium_count,
    COUNT(*) FILTER (WHERE severity = 'low')::INTEGER          AS low_count
FROM alerts
WHERE status = 'active';

-- ═══════════════════════════════════════════════════════════════════════════
--  ROW LEVEL SECURITY  (optional but recommended)
-- ═══════════════════════════════════════════════════════════════════════════
-- Uncomment if you add authentication:
-- ALTER TABLE agents              ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE calls               ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE quality_scores      ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE alerts              ENABLE ROW LEVEL SECURITY;

-- ═══════════════════════════════════════════════════════════════════════════
-- Verification: run this SELECT to confirm all tables exist
-- ═══════════════════════════════════════════════════════════════════════════
SELECT
    table_name,
    (SELECT COUNT(*) FROM information_schema.columns
     WHERE table_name = t.table_name AND table_schema = 'public') AS columns
FROM information_schema.tables t
WHERE table_schema = 'public'
  AND table_type  = 'BASE TABLE'
  AND table_name IN (
      'agents','calls','quality_scores','sentiment_analysis',
      'transcripts','alerts','unparliamentary_hits',
      'policy_documents','policy_chunks','policy_violations'
  )
ORDER BY table_name;
