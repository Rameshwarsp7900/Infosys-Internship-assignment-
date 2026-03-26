"""
database/db.py
──────────────
SQLite database manager for Criterion QA.
Handles all persistence: agents, calls, scores, sentiment, alerts, RAG policy.

Schema is auto-created on first run.
Works locally and on Vercel (/tmp/criterion.db).
"""

import os
import json
import sqlite3
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
from pathlib import Path

IS_VERCEL = os.getenv("VERCEL") == "1"
DB_PATH   = "/tmp/criterion.db" if IS_VERCEL else str(Path(__file__).parent.parent / "criterion.db")


# ─────────────────────────────────────────────────────────────
#  Connection
# ─────────────────────────────────────────────────────────────
@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
#  Schema Bootstrap
# ─────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    agent_id    TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    team        TEXT DEFAULT 'General',
    email       TEXT DEFAULT '',
    total_calls INTEGER DEFAULT 0,
    avg_score   REAL    DEFAULT 0.0,
    last_call_at TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS calls (
    call_id          TEXT PRIMARY KEY,
    agent_id         TEXT NOT NULL,
    customer_id      TEXT DEFAULT '',
    call_date        TEXT,
    filename         TEXT,
    file_type        TEXT,
    duration_seconds REAL,
    word_count       INTEGER DEFAULT 0,
    chunk_count      INTEGER DEFAULT 1,
    status           TEXT DEFAULT 'pending',
    processing_time  REAL,
    created_at       TEXT DEFAULT (datetime('now')),
    processed_at     TEXT,
    FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
);

CREATE TABLE IF NOT EXISTS quality_scores (
    call_id             TEXT PRIMARY KEY,
    overall_rating      REAL DEFAULT 0.0,
    rating_label        TEXT DEFAULT 'Average',
    empathy             REAL DEFAULT 0.0,
    resolution          REAL DEFAULT 0.0,
    communication       REAL DEFAULT 0.0,
    professionalism     REAL DEFAULT 0.0,
    product_knowledge   REAL DEFAULT 0.0,
    listening           REAL DEFAULT 0.0,
    response_time       REAL DEFAULT 0.0,
    customer_satisfaction REAL DEFAULT 0.0,
    first_call_resolution REAL DEFAULT 0.0,
    compliance          REAL DEFAULT 0.0,
    f1_score            REAL DEFAULT 0.0,
    precision_score     REAL DEFAULT 0.0,
    recall_score        REAL DEFAULT 0.0,
    summary             TEXT DEFAULT '',
    metrics_json        TEXT DEFAULT '{}',
    feedback_json       TEXT DEFAULT '{}',
    created_at          TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (call_id) REFERENCES calls(call_id)
);

CREATE TABLE IF NOT EXISTS sentiment_analysis (
    call_id             TEXT PRIMARY KEY,
    overall_sentiment   TEXT DEFAULT 'neutral',
    agent_sentiment     TEXT DEFAULT 'neutral',
    customer_sentiment  TEXT DEFAULT 'neutral',
    avg_agent_score     REAL DEFAULT 0.0,
    avg_customer_score  REAL DEFAULT 0.0,
    escalation_detected INTEGER DEFAULT 0,
    escalation_point    REAL,
    timeline_json       TEXT DEFAULT '[]',
    created_at          TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (call_id) REFERENCES calls(call_id)
);

CREATE TABLE IF NOT EXISTS transcripts (
    call_id    TEXT PRIMARY KEY,
    full_text  TEXT DEFAULT '',
    segments_json TEXT DEFAULT '[]',
    formatted  TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (call_id) REFERENCES calls(call_id)
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id   TEXT PRIMARY KEY,
    call_id    TEXT,
    agent_id   TEXT,
    alert_type TEXT,
    severity   TEXT DEFAULT 'medium',
    title      TEXT,
    message    TEXT,
    details_json TEXT DEFAULT '{}',
    status     TEXT DEFAULT 'active',
    created_at TEXT DEFAULT (datetime('now')),
    dismissed_at TEXT,
    FOREIGN KEY (call_id) REFERENCES calls(call_id)
);

CREATE TABLE IF NOT EXISTS unparliamentary_hits (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id    TEXT,
    word       TEXT,
    timestamp  REAL,
    speaker    TEXT,
    context    TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (call_id) REFERENCES calls(call_id)
);

CREATE TABLE IF NOT EXISTS policy_documents (
    doc_id     TEXT PRIMARY KEY,
    title      TEXT,
    content    TEXT,
    chunks_json TEXT DEFAULT '[]',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS policy_violations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id    TEXT,
    doc_id     TEXT,
    rule_text  TEXT,
    violation  TEXT,
    severity   TEXT DEFAULT 'medium',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (call_id) REFERENCES calls(call_id)
);

CREATE INDEX IF NOT EXISTS idx_calls_agent    ON calls(agent_id);
CREATE INDEX IF NOT EXISTS idx_calls_date     ON calls(call_date);
CREATE INDEX IF NOT EXISTS idx_calls_status   ON calls(status);
CREATE INDEX IF NOT EXISTS idx_alerts_agent   ON alerts(agent_id);
CREATE INDEX IF NOT EXISTS idx_alerts_status  ON alerts(status);
CREATE INDEX IF NOT EXISTS idx_alerts_type    ON alerts(alert_type);
"""

def init_db():
    """Create all tables if they don't exist."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ─────────────────────────────────────────────────────────────
#  Agent operations
# ─────────────────────────────────────────────────────────────
def upsert_agent(agent_id: str, name: str = "", team: str = "General", email: str = ""):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO agents (agent_id, name, team, email)
            VALUES (?,?,?,?)
            ON CONFLICT(agent_id) DO UPDATE SET
                name  = CASE WHEN excluded.name != '' THEN excluded.name ELSE agents.name END,
                team  = CASE WHEN excluded.team != 'General' THEN excluded.team ELSE agents.team END
        """, (agent_id, name, team, email))


def get_agent(agent_id: str) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        return dict(row) if row else None


def get_all_agents() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT a.*,
                   COUNT(c.call_id) as call_count,
                   AVG(q.overall_rating) as computed_avg,
                   SUM(CASE WHEN al.status='active' THEN 1 ELSE 0 END) as active_alerts
            FROM agents a
            LEFT JOIN calls c ON c.agent_id = a.agent_id
            LEFT JOIN quality_scores q ON q.call_id = c.call_id
            LEFT JOIN alerts al ON al.agent_id = a.agent_id AND al.status='active'
            GROUP BY a.agent_id
            ORDER BY computed_avg DESC NULLS LAST
        """).fetchall()
        return [dict(r) for r in rows]


def update_agent_stats(agent_id: str):
    """Recompute and store aggregated stats for an agent."""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) as cnt, AVG(q.overall_rating) as avg, MAX(c.call_date) as last
            FROM calls c
            LEFT JOIN quality_scores q ON q.call_id=c.call_id
            WHERE c.agent_id=?
        """, (agent_id,)).fetchone()
        if row:
            conn.execute("""
                UPDATE agents SET total_calls=?, avg_score=?, last_call_at=?
                WHERE agent_id=?
            """, (row["cnt"], round(row["avg"] or 0, 2), row["last"], agent_id))


# ─────────────────────────────────────────────────────────────
#  Call operations
# ─────────────────────────────────────────────────────────────
def insert_call(call_id, agent_id, customer_id, call_date, filename, file_type,
                duration=None, word_count=0, chunk_count=1):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO calls
            (call_id,agent_id,customer_id,call_date,filename,file_type,
             duration_seconds,word_count,chunk_count,status)
            VALUES (?,?,?,?,?,?,?,?,?,'processing')
        """, (call_id,agent_id,customer_id,call_date,filename,file_type,
              duration,word_count,chunk_count))


def update_call_status(call_id: str, status: str, processing_time: float = None):
    with get_conn() as conn:
        conn.execute("""
            UPDATE calls SET status=?, processing_time=?, processed_at=datetime('now')
            WHERE call_id=?
        """, (status, processing_time, call_id))


def get_call(call_id: str) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM calls WHERE call_id=?", (call_id,)).fetchone()
        return dict(row) if row else None


def get_agent_calls(agent_id: str, limit=50) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT c.*, q.overall_rating, q.rating_label, q.f1_score, q.compliance,
                   s.overall_sentiment, s.escalation_detected
            FROM calls c
            LEFT JOIN quality_scores q ON q.call_id=c.call_id
            LEFT JOIN sentiment_analysis s ON s.call_id=c.call_id
            WHERE c.agent_id=?
            ORDER BY c.created_at DESC LIMIT ?
        """, (agent_id, limit)).fetchall()
        return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────
#  Quality scores
# ─────────────────────────────────────────────────────────────
def insert_quality_scores(call_id: str, qa: dict):
    m = qa.get("metrics", {})
    feedback = {
        "critical_issues": qa.get("critical_issues", []),
        "positive_highlights": qa.get("positive_highlights", []),
        "improvement_suggestions": qa.get("improvement_suggestions", []),
    }
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO quality_scores
            (call_id,overall_rating,rating_label,
             empathy,resolution,communication,professionalism,product_knowledge,
             listening,response_time,customer_satisfaction,first_call_resolution,compliance,
             f1_score,precision_score,recall_score,summary,metrics_json,feedback_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            call_id,
            qa.get("overall_rating", 0),
            qa.get("rating_label", "Average"),
            m.get("empathy", {}).get("score", 0),
            m.get("resolution", {}).get("score", 0),
            m.get("communication", {}).get("score", 0),
            m.get("professionalism", {}).get("score", 0),
            m.get("product_knowledge", {}).get("score", 0),
            m.get("listening", {}).get("score", 0),
            m.get("response_time", {}).get("score", 0),
            m.get("customer_satisfaction", {}).get("score", 0),
            m.get("first_call_resolution", {}).get("score", 0),
            m.get("compliance", {}).get("score", 0),
            qa.get("f1_score", 0),
            qa.get("precision", 0),
            qa.get("recall", 0),
            qa.get("summary", ""),
            json.dumps(m),
            json.dumps(feedback),
        ))


def get_quality_scores(call_id: str) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM quality_scores WHERE call_id=?", (call_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["metrics"]  = json.loads(d.pop("metrics_json", "{}"))
        d["feedback"]  = json.loads(d.pop("feedback_json", "{}"))
        return d


# ─────────────────────────────────────────────────────────────
#  Sentiment
# ─────────────────────────────────────────────────────────────
def insert_sentiment(call_id: str, sentiment_data: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO sentiment_analysis
            (call_id,overall_sentiment,agent_sentiment,customer_sentiment,
             avg_agent_score,avg_customer_score,escalation_detected,
             escalation_point,timeline_json)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            call_id,
            sentiment_data.get("overall", "neutral"),
            sentiment_data.get("agent", "neutral"),
            sentiment_data.get("customer", "neutral"),
            sentiment_data.get("avg_agent_score", 0),
            sentiment_data.get("avg_customer_score", 0),
            1 if sentiment_data.get("escalation_detected") else 0,
            sentiment_data.get("escalation_point"),
            json.dumps(sentiment_data.get("timeline", [])),
        ))


def get_sentiment(call_id: str) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sentiment_analysis WHERE call_id=?", (call_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["timeline"] = json.loads(d.pop("timeline_json", "[]"))
        # Normalize column names to short form for API consistency
        d["overall"]  = d.get("overall_sentiment",  "neutral")
        d["agent"]    = d.get("agent_sentiment",    "neutral")
        d["customer"] = d.get("customer_sentiment", "neutral")
        d["escalation_detected"] = bool(d.get("escalation_detected", 0))
        return d


# ─────────────────────────────────────────────────────────────
#  Transcripts
# ─────────────────────────────────────────────────────────────
def insert_transcript(call_id: str, transcript: dict, formatted: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO transcripts (call_id,full_text,segments_json,formatted)
            VALUES (?,?,?,?)
        """, (
            call_id,
            transcript.get("full_text", ""),
            json.dumps(transcript.get("segments", [])),
            formatted,
        ))


def get_transcript(call_id: str) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM transcripts WHERE call_id=?", (call_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["segments"] = json.loads(d.pop("segments_json", "[]"))
        return d


# ─────────────────────────────────────────────────────────────
#  Alerts
# ─────────────────────────────────────────────────────────────
def insert_alert(call_id: str, agent_id: str, alert_type: str, severity: str,
                 title: str, message: str, details: dict = None):
    alert_id = uuid.uuid4().hex
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO alerts (alert_id,call_id,agent_id,alert_type,severity,title,message,details_json)
            VALUES (?,?,?,?,?,?,?,?)
        """, (alert_id, call_id, agent_id, alert_type, severity, title, message,
              json.dumps(details or {})))
    return alert_id


def get_alerts(status="active", agent_id=None, limit=100) -> List[Dict]:
    with get_conn() as conn:
        q = "SELECT a.*, c.filename FROM alerts a LEFT JOIN calls c ON c.call_id=a.call_id WHERE a.status=?"
        params = [status]
        if agent_id:
            q += " AND a.agent_id=?"
            params.append(agent_id)
        q += " ORDER BY a.created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["details"] = json.loads(d.pop("details_json", "{}"))
            result.append(d)
        return result


def dismiss_alert(alert_id: str):
    with get_conn() as conn:
        conn.execute("""
            UPDATE alerts SET status='dismissed', dismissed_at=datetime('now')
            WHERE alert_id=?
        """, (alert_id,))


def get_alert_summary() -> Dict:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT alert_type, severity, COUNT(*) as cnt
            FROM alerts WHERE status='active'
            GROUP BY alert_type, severity
        """).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM alerts WHERE status='active'").fetchone()[0]
        critical = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE status='active' AND severity='critical'"
        ).fetchone()[0]
        return {
            "total_active": total,
            "critical_count": critical,
            "breakdown": [dict(r) for r in rows],
        }


# ─────────────────────────────────────────────────────────────
#  Unparliamentary words
# ─────────────────────────────────────────────────────────────
def insert_unparliamentary_hits(call_id: str, hits: List[Dict]):
    with get_conn() as conn:
        conn.executemany("""
            INSERT INTO unparliamentary_hits (call_id,word,timestamp,speaker,context)
            VALUES (?,?,?,?,?)
        """, [(call_id, h["word"], h.get("timestamp"), h.get("speaker"), h.get("context", "")) for h in hits])


def get_unparliamentary_hits(call_id: str) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM unparliamentary_hits WHERE call_id=? ORDER BY timestamp",
            (call_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────
#  Policy documents
# ─────────────────────────────────────────────────────────────
def insert_policy_doc(title: str, content: str, chunks: List[str]) -> str:
    doc_id = uuid.uuid4().hex
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO policy_documents (doc_id,title,content,chunks_json)
            VALUES (?,?,?,?)
        """, (doc_id, title, content, json.dumps(chunks)))
    return doc_id


def get_all_policy_docs() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT doc_id,title,created_at FROM policy_documents ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def delete_policy_doc(doc_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM policy_documents WHERE doc_id=?", (doc_id,))
        return cur.rowcount > 0


def get_policy_chunks() -> List[Dict]:
    """Return all policy chunks for RAG retrieval."""
    with get_conn() as conn:
        rows = conn.execute("SELECT doc_id, title, chunks_json FROM policy_documents").fetchall()
        result = []
        for r in rows:
            chunks = json.loads(r["chunks_json"])
            for i, chunk in enumerate(chunks):
                result.append({"doc_id": r["doc_id"], "title": r["title"], "text": chunk, "chunk_idx": i})
        return result


def insert_policy_violation(call_id, doc_id, rule_text, violation, severity="medium"):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO policy_violations (call_id,doc_id,rule_text,violation,severity)
            VALUES (?,?,?,?,?)
        """, (call_id, doc_id, rule_text, violation, severity))


def get_policy_violations(call_id: str) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM policy_violations WHERE call_id=? ORDER BY id",
            (call_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────
#  Analytics summary
# ─────────────────────────────────────────────────────────────
def get_system_summary(from_date=None, to_date=None) -> Dict:
    with get_conn() as conn:
        base = "FROM calls c LEFT JOIN quality_scores q ON q.call_id=c.call_id"
        where = []
        params = []
        if from_date:
            where.append("c.call_date >= ?"); params.append(from_date)
        if to_date:
            where.append("c.call_date <= ?"); params.append(to_date)
        wclause = (" WHERE " + " AND ".join(where)) if where else ""

        summary = conn.execute(
            f"SELECT COUNT(*) as total_calls, AVG(q.overall_rating) as avg_score,"
            f" AVG(q.f1_score) as avg_f1 {base}{wclause}",
            params
        ).fetchone()

        by_agent = conn.execute(f"""
            SELECT c.agent_id, a.name, COUNT(*) as call_count,
                   AVG(q.overall_rating) as avg_score,
                   AVG(q.compliance) as avg_compliance,
                   SUM(CASE WHEN al.severity='critical' THEN 1 ELSE 0 END) as critical_alerts
            {base}
            LEFT JOIN agents a ON a.agent_id=c.agent_id
            LEFT JOIN alerts al ON al.call_id=c.call_id
            {wclause}
            GROUP BY c.agent_id ORDER BY avg_score DESC NULLS LAST
        """, params).fetchall()

        trend = conn.execute(f"""
            SELECT c.call_date, COUNT(*) as calls, AVG(q.overall_rating) as avg_score
            {base}{wclause}
            GROUP BY c.call_date ORDER BY c.call_date DESC LIMIT 30
        """, params).fetchall()

        return {
            "total_calls": summary["total_calls"] or 0,
            "avg_score":   round(summary["avg_score"] or 0, 2),
            "avg_f1":      round(summary["avg_f1"] or 0, 3),
            "by_agent":    [dict(r) for r in by_agent],
            "trend":       [dict(r) for r in trend],
        }
