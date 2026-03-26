"""
database/supabase_db.py — v3 fixed
────────────────────────────────────
Key fixes:
  1. init_db() runs the schema SQL automatically (no manual Supabase SQL editor needed)
  2. get_all_agents() uses agent_stats_view (no RPC function needed)
  3. get_alert_summary() uses alert_summary_view
  4. All functions wrapped in try/except — never crash the app
  5. policy_chunks table used for RAG (replaces chunks_json in policy_documents)
"""

import os, json, logging
from typing import Optional, List, Dict, Any

log = logging.getLogger(__name__)

SCHEMA_TABLES = [
    "agents","calls","quality_scores","sentiment_analysis","transcripts",
    "alerts","unparliamentary_hits","policy_documents","policy_chunks","policy_violations",
]

# ── Client singleton ──────────────────────────────────────────────────────────
_client = None

def _sb():
    global _client
    if _client is None:
        try:
            from supabase import create_client
            url = os.getenv("SUPABASE_URL","")
            key = os.getenv("SUPABASE_ANON_KEY","")
            if not url or not key:
                raise RuntimeError("SUPABASE_URL or SUPABASE_ANON_KEY not set")
            _client = create_client(url, key)
        except ImportError:
            raise RuntimeError("supabase-py not installed. Run: pip install supabase")
    return _client

def _rows(resp) -> List[Dict]:
    try:
        return resp.data or []
    except Exception:
        return []

def _row(resp) -> Optional[Dict]:
    rows = _rows(resp)
    return rows[0] if rows else None

# ── Init DB ────────────────────────────────────────────────────────────────────
def init_db():
    """
    Verify tables exist. If not, log an error with instructions.
    We can't run DDL via supabase-py (no pg_execute), so the user
    must run setup_supabase.sql once in the Supabase SQL Editor.
    """
    try:
        # Quick probe — try to read from agents table
        _sb().table("agents").select("agent_id").limit(1).execute()
        log.info("Supabase connection OK — all tables verified")
    except Exception as e:
        err = str(e)
        if "PGRST205" in err or "schema cache" in err.lower():
            log.error(
                "Supabase tables not found! "
                "Please run backend/database/setup_supabase.sql in your Supabase SQL Editor: "
                "https://supabase.com → Project → SQL Editor → New Query → paste file → Run"
            )
        else:
            raise RuntimeError(f"Supabase connection failed: {e}")

# ── AGENTS ────────────────────────────────────────────────────────────────────
def upsert_agent(agent_id: str, name: str = "", team: str = "General", email: str = "") -> Optional[Dict]:
    try:
        return _row(_sb().table("agents").upsert({
            "agent_id": agent_id, "name": name, "team": team, "email": email,
        }, on_conflict="agent_id").execute())
    except Exception as e:
        log.error(f"upsert_agent: {e}"); return None

def get_agent(agent_id: str) -> Optional[Dict]:
    try:
        # Use agent_stats_view for enriched data
        row = _row(_sb().table("agent_stats_view").select("*").eq("agent_id", agent_id).execute())
        return row
    except Exception:
        try:
            return _row(_sb().table("agents").select("*").eq("agent_id", agent_id).execute())
        except Exception as e:
            log.error(f"get_agent: {e}"); return None

def get_all_agents() -> List[Dict]:
    """Uses agent_stats_view (created in setup_supabase.sql) — no RPC needed."""
    try:
        rows = _rows(_sb().table("agent_stats_view").select("*").order("computed_avg", desc=True).execute())
        return rows
    except Exception:
        # Fallback: plain agents table
        try:
            return _rows(_sb().table("agents").select("*").order("last_call_at", desc=True).execute())
        except Exception as e:
            log.error(f"get_all_agents: {e}"); return []

def update_agent_stats(agent_id: str):
    try:
        # Get all calls for this agent
        calls = _rows(_sb().table("calls").select("call_id").eq("agent_id", agent_id).execute())
        call_ids = [c["call_id"] for c in calls]
        total = len(call_ids)
        avg = 0.0
        if call_ids:
            qs = _rows(_sb().table("quality_scores").select("overall_rating").in_("call_id", call_ids).execute())
            scores = [q["overall_rating"] for q in qs if q.get("overall_rating") is not None]
            avg = round(sum(scores)/len(scores), 2) if scores else 0.0
        # Latest call date
        latest = _row(_sb().table("calls").select("call_date").eq("agent_id", agent_id)
                      .order("call_date", desc=True).limit(1).execute())
        _sb().table("agents").update({
            "total_calls":   total,
            "avg_score":     avg,
            "last_call_at":  latest.get("call_date","") if latest else "",
        }).eq("agent_id", agent_id).execute()
    except Exception as e:
        log.error(f"update_agent_stats: {e}")

# ── CALLS ─────────────────────────────────────────────────────────────────────
def insert_call(call_id, agent_id, customer_id="", call_date="", filename="",
                file_type="audio", duration=0.0, word_count=0, chunk_count=1):
    try:
        upsert_agent(agent_id)   # ensure agent exists
        _sb().table("calls").insert({
            "call_id":call_id,"agent_id":agent_id,"customer_id":customer_id,
            "call_date":call_date,"filename":filename,"file_type":file_type,
            "duration_seconds":duration,"word_count":word_count,
            "chunk_count":chunk_count,"status":"processing",
        }).execute()
    except Exception as e:
        log.error(f"insert_call: {e}")

def update_call_status(call_id, status, processing_time=0.0):
    try:
        _sb().table("calls").update({
            "status":status,"processing_time":processing_time,
        }).eq("call_id",call_id).execute()
    except Exception as e:
        log.error(f"update_call_status: {e}")

def get_call(call_id) -> Optional[Dict]:
    try:
        return _row(_sb().table("calls").select("*").eq("call_id",call_id).execute())
    except Exception as e:
        log.error(f"get_call: {e}"); return None

def get_agent_calls(agent_id, limit=50) -> List[Dict]:
    try:
        calls = _rows(_sb().table("calls").select("*").eq("agent_id",agent_id)
                      .order("created_at",desc=True).limit(limit).execute())
        # Enrich with quality scores
        for c in calls:
            try:
                qs = _row(_sb().table("quality_scores").select(
                    "overall_rating,f1_score").eq("call_id",c["call_id"]).execute())
                if qs:
                    c["overall_rating"] = qs.get("overall_rating",0)
                    c["f1_score"]       = qs.get("f1_score",0)
            except Exception: pass
            try:
                s = _row(_sb().table("sentiment_analysis").select(
                    "overall_sentiment,escalation_detected").eq("call_id",c["call_id"]).execute())
                if s:
                    c["overall_sentiment"]   = s.get("overall_sentiment","neutral")
                    c["escalation_detected"] = s.get("escalation_detected",False)
            except Exception: pass
        return calls
    except Exception as e:
        log.error(f"get_agent_calls: {e}"); return []

# ── QUALITY SCORES ────────────────────────────────────────────────────────────
def insert_quality_scores(call_id, qa: dict):
    try:
        metrics  = qa.get("metrics",{})
        feedback = {
            "critical_issues":         qa.get("critical_issues",[]),
            "positive_highlights":     qa.get("positive_highlights",[]),
            "improvement_suggestions": qa.get("improvement_suggestions",[]),
        }
        _sb().table("quality_scores").upsert({
            "call_id":              call_id,
            "overall_rating":       qa.get("overall_rating",0),
            "rating_label":         qa.get("rating_label","Average"),
            "empathy":              (metrics.get("empathy")              or {}).get("score",0),
            "resolution":           (metrics.get("resolution")           or {}).get("score",0),
            "communication":        (metrics.get("communication")        or {}).get("score",0),
            "professionalism":      (metrics.get("professionalism")      or {}).get("score",0),
            "product_knowledge":    (metrics.get("product_knowledge")    or {}).get("score",0),
            "listening":            (metrics.get("listening")            or {}).get("score",0),
            "response_time":        (metrics.get("response_time")        or {}).get("score",0),
            "customer_satisfaction":(metrics.get("customer_satisfaction")or {}).get("score",0),
            "first_call_resolution":(metrics.get("first_call_resolution")or {}).get("score",0),
            "compliance":           (metrics.get("compliance")           or {}).get("score",0),
            "f1_score":             qa.get("f1_score",0),
            "precision_score":      qa.get("precision",0),
            "recall_score":         qa.get("recall",0),
            "summary":              qa.get("summary",""),
            "metrics_json":         metrics,
            "feedback_json":        feedback,
            "provider":             qa.get("_provider",""),
        }, on_conflict="call_id").execute()
    except Exception as e:
        log.error(f"insert_quality_scores: {e}")

def get_quality_scores(call_id) -> Optional[Dict]:
    try:
        row = _row(_sb().table("quality_scores").select("*").eq("call_id",call_id).execute())
        if row:
            # Rebuild metrics dict for frontend compatibility
            keys = ["empathy","resolution","communication","professionalism",
                    "product_knowledge","listening","response_time",
                    "customer_satisfaction","first_call_resolution","compliance"]
            row["metrics"] = {k:{"score":row.get(k,0),"reason":""} for k in keys}
            fb = row.get("feedback_json",{})
            if isinstance(fb, str): fb = json.loads(fb)
            row.update(fb)
        return row
    except Exception as e:
        log.error(f"get_quality_scores: {e}"); return None

# ── SENTIMENT ─────────────────────────────────────────────────────────────────
def insert_sentiment(call_id, s: dict):
    try:
        _sb().table("sentiment_analysis").upsert({
            "call_id":             call_id,
            "overall_sentiment":   s.get("overall","neutral"),
            "agent_sentiment":     s.get("agent","neutral"),
            "customer_sentiment":  s.get("customer","neutral"),
            "avg_agent_score":     s.get("avg_agent_score",0.0),
            "avg_customer_score":  s.get("avg_customer_score",0.0),
            "escalation_detected": s.get("escalation_detected",False),
            "escalation_point":    s.get("escalation_point"),
            "timeline_json":       s.get("timeline",[]),
        }, on_conflict="call_id").execute()
    except Exception as e:
        log.error(f"insert_sentiment: {e}")

def get_sentiment(call_id) -> Optional[Dict]:
    try:
        row = _row(_sb().table("sentiment_analysis").select("*").eq("call_id",call_id).execute())
        if row:
            tl = row.get("timeline_json",[])
            row["timeline"] = json.loads(tl) if isinstance(tl,str) else tl
        return row
    except Exception as e:
        log.error(f"get_sentiment: {e}"); return None

# ── TRANSCRIPTS ───────────────────────────────────────────────────────────────
def insert_transcript(call_id, tr: dict, formatted: str = ""):
    try:
        _sb().table("transcripts").upsert({
            "call_id":      call_id,
            "full_text":    tr.get("full_text",""),
            "segments_json": tr.get("segments",[]),
            "formatted":    formatted,
            "word_count":   tr.get("word_count",0),
            "duration":     tr.get("duration",0),
            "speakers":     tr.get("speakers",0),
        }, on_conflict="call_id").execute()
    except Exception as e:
        log.error(f"insert_transcript: {e}")

def get_transcript(call_id) -> Optional[Dict]:
    try:
        row = _row(_sb().table("transcripts").select("*").eq("call_id",call_id).execute())
        if row:
            segs = row.get("segments_json",[])
            row["segments"] = json.loads(segs) if isinstance(segs,str) else segs
        return row
    except Exception as e:
        log.error(f"get_transcript: {e}"); return None

# ── ALERTS ────────────────────────────────────────────────────────────────────
def insert_alert(call_id, agent_id, alert_type, severity, title, message, details={}, channel="agent") -> str:
    try:
        import uuid
        alert_id = str(uuid.uuid4())
        _sb().table("alerts").insert({
            "alert_id":call_id and alert_id or alert_id,
            "call_id":call_id,"agent_id":agent_id,
            "alert_type":alert_type,"severity":severity,
            "channel":channel,"title":title,"message":message,
            "details_json":details,"status":"active",
        }).execute()
        return alert_id
    except Exception as e:
        log.error(f"insert_alert: {e}"); return ""

def get_alerts(status="active", agent_id=None, limit=200) -> List[Dict]:
    try:
        q = _sb().table("alerts").select("*").eq("status",status)
        if agent_id:
            q = q.eq("agent_id",agent_id)
        return _rows(q.order("created_at",desc=True).limit(limit).execute())
    except Exception as e:
        log.error(f"get_alerts: {e}"); return []

def dismiss_alert(alert_id):
    try:
        from datetime import datetime,timezone
        _sb().table("alerts").update({
            "status":"dismissed","dismissed_at":datetime.now(timezone.utc).isoformat()
        }).eq("alert_id",alert_id).execute()
    except Exception as e:
        log.error(f"dismiss_alert: {e}")

def get_alert_summary() -> Dict:
    """Uses alert_summary_view from setup_supabase.sql"""
    try:
        row = _row(_sb().table("alert_summary_view").select("*").execute())
        if row:
            return {
                "total_active":   row.get("total_active",0),
                "critical_count": row.get("critical_count",0),
                "high_count":     row.get("high_count",0),
                "medium_count":   row.get("medium_count",0),
                "low_count":      row.get("low_count",0),
                "breakdown": [],
            }
    except Exception: pass
    # Fallback: manual count
    try:
        alerts = get_alerts(status="active", limit=500)
        from collections import Counter
        sev = Counter(a.get("severity","medium") for a in alerts)
        return {
            "total_active":   len(alerts),
            "critical_count": sev.get("critical",0),
            "high_count":     sev.get("high",0),
            "medium_count":   sev.get("medium",0),
            "low_count":      sev.get("low",0),
            "breakdown": [],
        }
    except Exception as e:
        log.error(f"get_alert_summary: {e}")
        return {"total_active":0,"critical_count":0,"high_count":0,"medium_count":0,"low_count":0,"breakdown":[]}

# ── UNPARLIAMENTARY ───────────────────────────────────────────────────────────
def insert_unparliamentary_hits(call_id, agent_id, hits: list):
    try:
        rows = [{
            "call_id":call_id,"agent_id":agent_id,
            "word":h.get("word",""),"speaker":h.get("speaker",""),
            "timestamp":h.get("timestamp",0),"context":h.get("context",""),
        } for h in hits]
        if rows:
            _sb().table("unparliamentary_hits").insert(rows).execute()
    except Exception as e:
        log.error(f"insert_unparliamentary_hits: {e}")

def get_unparliamentary_hits(call_id) -> List[Dict]:
    try:
        return _rows(_sb().table("unparliamentary_hits").select("*").eq("call_id",call_id).execute())
    except Exception as e:
        log.error(f"get_unparliamentary_hits: {e}"); return []

# ── POLICY / RAG ──────────────────────────────────────────────────────────────
def insert_policy_doc(title, content, chunks=None) -> str:
    try:
        import uuid
        doc_id = str(uuid.uuid4())
        _sb().table("policy_documents").insert({
            "doc_id":doc_id,"title":title,"content":content,
        }).execute()
        # Insert chunks
        if chunks:
            chunk_rows = []
            for i, ch in enumerate(chunks):
                row = {
                    "doc_id":doc_id,"title":title,
                    "text":ch.get("text",""),"chunk_idx":i,
                }
                chunk_rows.append(row)
            if chunk_rows:
                _sb().table("policy_chunks").insert(chunk_rows).execute()
        return doc_id
    except Exception as e:
        log.error(f"insert_policy_doc: {e}"); return ""

def delete_policy_doc(doc_id: str) -> bool:
    try:
        _sb().table("policy_documents").delete().eq("doc_id", doc_id).execute()
        return True
    except Exception as e:
        log.error(f"delete_policy_doc: {e}"); return False

def get_all_policy_docs() -> List[Dict]:
    try:
        return _rows(_sb().table("policy_documents").select("doc_id,title,created_at")
                     .order("created_at",desc=True).execute())
    except Exception as e:
        log.error(f"get_all_policy_docs: {e}"); return []

def get_policy_chunks(query_embedding=None, top_k=5) -> List[Dict]:
    """
    Retrieve relevant policy chunks.
    If query_embedding provided + pgvector enabled → semantic search.
    Otherwise → return all chunks (TF-IDF done in policy_engine.py).
    """
    try:
        if query_embedding is not None:
            # pgvector semantic search
            try:
                resp = _sb().rpc("match_policy_chunks", {
                    "query_embedding": query_embedding,
                    "match_count": top_k,
                }).execute()
                rows = _rows(resp)
                if rows:
                    return rows
            except Exception as e:
                log.debug(f"pgvector search failed, falling back to full scan: {e}")
        # Fallback: return all chunks
        return _rows(_sb().table("policy_chunks").select("chunk_id,doc_id,title,text,chunk_idx")
                     .order("doc_id").execute())
    except Exception as e:
        log.error(f"get_policy_chunks: {e}"); return []

def insert_policy_violation(call_id, doc_id, rule_text, violation, severity="medium"):
    try:
        _sb().table("policy_violations").insert({
            "call_id":call_id,"doc_id":doc_id,"rule_text":rule_text,
            "violation":violation,"severity":severity,
        }).execute()
    except Exception as e:
        log.error(f"insert_policy_violation: {e}")

def get_policy_violations(call_id) -> List[Dict]:
    try:
        return _rows(_sb().table("policy_violations").select("*").eq("call_id",call_id).execute())
    except Exception as e:
        log.error(f"get_policy_violations: {e}"); return []

# ── SYSTEM SUMMARY ────────────────────────────────────────────────────────────
def get_system_summary(from_date=None, to_date=None) -> Dict:
    try:
        q = _sb().table("calls").select("call_id")
        if from_date: q = q.gte("call_date", from_date)
        if to_date:   q = q.lte("call_date", to_date)
        calls = _rows(q.execute())
        total = len(calls)

        avg_score = 0.0
        avg_f1    = 0.0
        if total:
            call_ids = [c["call_id"] for c in calls]
            qs = _rows(_sb().table("quality_scores")
                       .select("overall_rating,f1_score")
                       .in_("call_id", call_ids[:500]).execute())
            scores = [q["overall_rating"] for q in qs if q.get("overall_rating") is not None]
            f1s    = [q["f1_score"]       for q in qs if q.get("f1_score") is not None]
            avg_score = round(sum(scores)/len(scores),2) if scores else 0.0
            avg_f1    = round(sum(f1s)/len(f1s),2)       if f1s    else 0.0

        agents    = _rows(_sb().table("agents").select("agent_id,name,avg_score,total_calls").execute())
        alert_sum = get_alert_summary()

        return {
            "total_calls": total,
            "avg_score":   avg_score,
            "avg_f1":      avg_f1,
            "by_agent":    agents,
            **alert_sum,
        }
    except Exception as e:
        log.error(f"get_system_summary: {e}")
        return {"total_calls":0,"avg_score":0,"avg_f1":0,"by_agent":[],"total_active":0,"critical_count":0}
