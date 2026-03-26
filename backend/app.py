"""
backend/app.py — Criterion QA v3
──────────────────────────────────
Backend serves the frontend from ../frontend/.
All API endpoints return JSON. Never returns 422 from a pipeline error.
"""

import os, uuid, sys
from pathlib import Path
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

# ── Path setup ────────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"
STATIC_DIR   = FRONTEND_DIR / "static"
TEMPLATE_DIR = FRONTEND_DIR / "templates"

env_path = BASE_DIR / "config" / ".env"
if env_path.exists():
    load_dotenv(env_path)

IS_VERCEL  = os.getenv("VERCEL") == "1"

# ── Logging ─────────────────────────────────────────────────────
import sys
sys.path.insert(0, str(BASE_DIR))
try:
    from logger_setup import get_logger, log_api_request, log_system_error
    _log = get_logger("criterion.api")
except ImportError:
    import logging; _log = logging.getLogger("criterion.api")
    def log_api_request(*a,**k): pass
    def log_system_error(*a,**k): pass
UPLOAD_DIR = "/tmp/uploads" if IS_VERCEL else str(BASE_DIR / "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

sys.path.insert(0, str(BASE_DIR))

# ── Flask ────────────────────────────────────────────────────
app = Flask(
    __name__,
    template_folder=str(TEMPLATE_DIR),
    static_folder=str(STATIC_DIR),
    static_url_path="/static",
)
app.secret_key = os.getenv("SECRET_KEY", "criterion-v3-dev")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024   # 200 MB
CORS(app)

@app.before_request
def _before():
    import time; request._t0 = time.time()

@app.after_request
def _after(response):
    elapsed = int((getattr(request,'_t0',0) and (__import__('time').time()-request._t0))*1000)
    log_api_request(request.method, request.path, response.status_code, elapsed)
    return response

@app.errorhandler(Exception)
def _handle_exc(e):
    log_system_error("flask", str(e))
    _log.error("Unhandled exception", extra={"error":str(e)})
    return {"error": "Internal server error", "status": "error"}, 500

ALLOWED_EXTENSIONS = {"mp3","m4a","wav","ogg","flac","webm","txt","log","pdf"}

# ── Database ─────────────────────────────────────────────────
from database.db_factory import db as _db_getter
db = _db_getter()
db.init_db()

# ── Job queue ─────────────────────────────────────────────────
from jobs.job_queue import create_job, submit_job, get_job, get_all_jobs, job_to_response

# ─────────────────────────────────────────────────────────────
#  Lazy pipeline + batch processor
# ─────────────────────────────────────────────────────────────
_pipeline = None
_batch    = None

def get_pipeline():
    global _pipeline
    if _pipeline is None:
        from transcription_pipeline import TranscriptionPipeline
        _pipeline = TranscriptionPipeline(
            deepgram_api_key   = os.getenv("DEEPGRAM_API_KEY"),
            openrouter_api_key = os.getenv("OPENROUTER_API_KEY"),
            openrouter_model   = os.getenv("OPENROUTER_MODEL"),
            anthropic_api_key  = os.getenv("ANTHROPIC_API_KEY"),
            mistral_api_key    = os.getenv("MISTRAL_API_KEY"),
            db_module          = db,
        )
    return _pipeline

def get_batch():
    global _batch
    if _batch is None:
        from transcription_pipeline import BatchProcessor
        _batch = BatchProcessor(get_pipeline())
    return _batch

# ─────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────
def allowed(filename):
    return "." in filename and filename.rsplit(".",1)[1].lower() in ALLOWED_EXTENSIONS

def safe_name(filename):
    n = filename.replace(" ","_").replace("/","_").replace("..","")
    return f"{uuid.uuid4().hex[:8]}_{n}"

def ok(data, code=200):
    return jsonify(data), code

def err(msg, code=400):
    return jsonify({"error": msg, "status": "error"}), code

# ─────────────────────────────────────────────────────────────
#  PAGES — serve frontend SPA
# ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(str(TEMPLATE_DIR), "index.html")

@app.route("/static/<path:path>")
def serve_static(path):
    """Serve files from frontend/static/<path> — fixes double-static bug."""
    return send_from_directory(str(STATIC_DIR), path)

@app.route("/favicon.ico")
def favicon():
    return send_from_directory(str(STATIC_DIR / "assets"), "logo.png")

# ─────────────────────────────────────────────────────────────
#  HEALTH
# ─────────────────────────────────────────────────────────────
@app.route("/api/health")
def health():
    summary = {}
    try:
        summary = db.get_system_summary()
    except Exception:
        pass
    # Check which APIs are configured (true/false only — no names exposed to UI)
    return ok({
        "status":       "healthy",
        "version":      "3.0.0",
        "apis_ready":   bool(os.getenv("DEEPGRAM_API_KEY") and os.getenv("OPENROUTER_API_KEY")),
        "transcription": "ready" if os.getenv("DEEPGRAM_API_KEY") else "not configured",
        "scoring":       "ready" if os.getenv("OPENROUTER_API_KEY") else "not configured",
        "db":            "connected",
        "total_calls":   summary.get("total_calls",0),
        "avg_score":     summary.get("avg_score",0),
    })

# ─────────────────────────────────────────────────────────────
#  UPLOAD — SYNC (returns completed result, never 422)
# ─────────────────────────────────────────────────────────────
@app.route("/api/upload", methods=["POST"])
def upload_sync():
    if "file" not in request.files:
        return err("No file provided")
    f = request.files["file"]
    if not f.filename:
        return err("Empty filename")
    if not allowed(f.filename):
        return err(f"Unsupported type. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    fname = safe_name(f.filename)
    fpath = os.path.join(UPLOAD_DIR, fname)
    f.save(fpath)

    metadata = {
        "call_id":     uuid.uuid4().hex,
        "agent_id":    request.form.get("agent_id","agent_001").strip() or "agent_001",
        "agent_name":  request.form.get("agent_name","").strip(),
        "team":        request.form.get("team","General"),
        "customer_id": request.form.get("customer_id","customer_001"),
        "call_date":   request.form.get("call_date", datetime.now().strftime("%Y-%m-%d")),
        "filename":    f.filename,
    }

    try:
        result = get_pipeline().process(fpath, metadata)
        # ALWAYS return 200 — no more 422!
        return ok(result)
    except Exception as e:
        return ok({
            "status": "completed", "error": str(e),
            "call_id": metadata["call_id"],
            "quality_analysis": {"overall_rating":0,"rating_label":"Error","metrics":{},
                                  "summary":str(e),"f1_score":0},
            "warnings": [str(e)],
        })
    finally:
        try:
            os.remove(fpath)
        except Exception:
            pass

# ─────────────────────────────────────────────────────────────
#  UPLOAD — ASYNC (long files, returns job_id)
# ─────────────────────────────────────────────────────────────
@app.route("/api/upload/async", methods=["POST"])
def upload_async():
    if "file" not in request.files:
        return err("No file provided")
    f = request.files["file"]
    if not f.filename or not allowed(f.filename):
        return err("Invalid or missing file")

    fname = safe_name(f.filename)
    fpath = os.path.join(UPLOAD_DIR, fname)
    f.save(fpath)

    metadata = {
        "call_id":     uuid.uuid4().hex,
        "agent_id":    request.form.get("agent_id","agent_001").strip() or "agent_001",
        "agent_name":  request.form.get("agent_name",""),
        "customer_id": request.form.get("customer_id","customer_001"),
        "call_date":   request.form.get("call_date", datetime.now().strftime("%Y-%m-%d")),
        "team":        request.form.get("team","General"),
        "filename":    f.filename,
    }
    job_id = create_job(metadata["agent_id"], f.filename, metadata)
    submit_job(job_id, fpath, metadata, get_pipeline().process)
    return ok({"job_id": job_id, "status": "pending",
                "message": "Processing started. Poll /api/jobs/{job_id}."}, 202)

# ─────────────────────────────────────────────────────────────
#  BATCH (up to 500 files)
# ─────────────────────────────────────────────────────────────
@app.route("/api/batch", methods=["POST"])
def batch():
    files = request.files.getlist("files[]")
    if not files:
        return err("No files provided")

    mode      = request.form.get("mode","single_agent")
    global_aid= request.form.get("agent_id","agent_001").strip() or "agent_001"
    team      = request.form.get("team","General")

    jobs = []
    for i, f in enumerate(files):
        if not f.filename or not allowed(f.filename):
            continue
        fname = safe_name(f.filename)
        fpath = os.path.join(UPLOAD_DIR, fname)
        f.save(fpath)
        agent_id = f.filename.rsplit(".",1)[0] if mode=="multi_agent" else global_aid
        jobs.append({
            "file_path": fpath,
            "metadata": {
                "call_id":     uuid.uuid4().hex,
                "agent_id":    agent_id,
                "customer_id": f"customer_{i+1}",
                "call_date":   datetime.now().strftime("%Y-%m-%d"),
                "team":        team,
                "filename":    f.filename,
            }
        })

    results   = get_batch().process_batch(jobs)
    completed = sum(1 for r in results if r and r.get("status")=="completed")

    # Cleanup temp files
    for job in jobs:
        try:
            os.remove(job["file_path"])
        except Exception:
            pass

    return ok({
        "batch_results": results,
        "total": len(results),
        "completed": completed,
        "failed": len(results) - completed,
    })

# ─────────────────────────────────────────────────────────────
#  JOBS
# ─────────────────────────────────────────────────────────────
@app.route("/api/jobs")
def list_jobs():
    jobs = get_all_jobs(agent_id=request.args.get("agent_id"), limit=int(request.args.get("limit",50)))
    return ok({"jobs": [job_to_response(j) for j in jobs], "total": len(jobs)})

@app.route("/api/jobs/<job_id>")
def job_status(job_id):
    j = get_job(job_id)
    if not j:
        return err(f"Job {job_id} not found", 404)
    return ok(job_to_response(j))

# ─────────────────────────────────────────────────────────────
#  AGENTS
# ─────────────────────────────────────────────────────────────
@app.route("/api/agents")
def agents():
    return ok({"agents": db.get_all_agents()})

@app.route("/api/agents/<agent_id>")
def agent_detail(agent_id):
    agent = db.get_agent(agent_id)
    if not agent:
        return err(f"Agent '{agent_id}' not found", 404)
    calls  = db.get_agent_calls(agent_id, limit=50)
    alerts = db.get_alerts(status="active", agent_id=agent_id)
    return ok({"agent": agent, "calls": calls, "alerts": alerts})

@app.route("/api/agents", methods=["POST"])
def create_agent():
    d = request.json or {}
    if not d.get("agent_id"):
        return err("agent_id required")
    db.upsert_agent(d["agent_id"], d.get("name",""), d.get("team","General"), d.get("email",""))
    return ok({"agent_id": d["agent_id"], "status": "created"}, 201)

# ─────────────────────────────────────────────────────────────
#  CALLS
# ─────────────────────────────────────────────────────────────
@app.route("/api/calls/<call_id>")
def call_detail(call_id):
    call = db.get_call(call_id)
    if not call:
        return err(f"Call '{call_id}' not found", 404)
    return ok({
        "call":               call,
        "quality_scores":     db.get_quality_scores(call_id),
        "sentiment":          db.get_sentiment(call_id),
        "transcript":         db.get_transcript(call_id),
        "alerts":             [a for a in db.get_alerts(agent_id=call.get("agent_id")) if a.get("call_id")==call_id],
        "policy_violations":  db.get_policy_violations(call_id),
        "unparliamentary_hits": db.get_unparliamentary_hits(call_id),
    })

# ─────────────────────────────────────────────────────────────
#  ALERTS
# ─────────────────────────────────────────────────────────────
@app.route("/api/alerts")
def alerts():
    data = db.get_alerts(status=request.args.get("status","active"),
                         agent_id=request.args.get("agent_id"),
                         limit=int(request.args.get("limit",100)))
    return ok({"alerts": data, "summary": db.get_alert_summary()})

@app.route("/api/alerts/summary")
def alert_summary():
    return ok(db.get_alert_summary())

@app.route("/api/alerts/<alert_id>/dismiss", methods=["POST"])
def dismiss_alert(alert_id):
    db.dismiss_alert(alert_id)
    return ok({"alert_id": alert_id, "status": "dismissed"})

# ─────────────────────────────────────────────────────────────
#  POLICY / RAG
# ─────────────────────────────────────────────────────────────
@app.route("/api/policy")
def policy():
    return ok({"documents": db.get_all_policy_docs()})

@app.route("/api/policy/upload", methods=["POST"])
def policy_upload():
    title = request.form.get("title","Unnamed Policy")
    content = ""
    if "file" in request.files:
        f = request.files["file"]
        name = (f.filename or "").lower()
        if name.endswith(".pdf"):
            from rag.pdf_processor import extract_text_from_pdf
            tmp = os.path.join(UPLOAD_DIR, safe_name(f.filename))
            f.save(tmp)
            content = extract_text_from_pdf(tmp) or ""
            try: os.remove(tmp)
            except: pass
        else:
            content = f.read().decode("utf-8", errors="replace")
    elif request.json:
        content = request.json.get("content","")
        title   = request.json.get("title", title)

    if not content.strip():
        return err("No policy content")

    doc_id = get_pipeline().policy_engine.index_document(title, content)
    return ok({"doc_id": doc_id, "title": title, "status": "indexed"}, 201)

@app.route("/api/policy/search")
def policy_search():
    q = request.args.get("q","")
    if not q:
        return err("Query parameter 'q' required")
    chunks = get_pipeline().policy_engine.retrieve(q, top_k=5)
    return ok({"results": chunks, "query": q})

@app.route("/api/policy/<doc_id>", methods=["DELETE"])
def policy_delete(doc_id):
    """Delete a policy document by ID."""
    try:
        removed = db.delete_policy_doc(doc_id)
        if not removed:
            return err("Document not found", 404)
        # Also reload policy engine cache
        try:
            get_pipeline().policy_engine._reload()
        except Exception:
            pass
        return ok({"deleted": doc_id, "status": "ok"})
    except Exception as e:
        return err(str(e), 500)

# ─────────────────────────────────────────────────────────────
#  MISTRAL CHATBOT
# ─────────────────────────────────────────────────────────────
@app.route("/api/chatbot", methods=["POST"])
def chatbot():
    """Criterion QA assistant powered by Mistral via OpenRouter."""
    import requests as req
    body = request.json or {}
    messages = body.get("messages", [])
    if not messages:
        return err("messages required")

    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("MISTRAL_API_KEY", "")
    if not api_key:
        return err("No AI API key configured")

    # ── Fetch live agent data from DB ──────────────────────────
    agent_context = ""
    try:
        agents_data = db.get_all_agents()
        summary     = db.get_system_summary()
        alert_sum   = db.get_alert_summary()

        if agents_data:
            lines = [f"\nLIVE AGENT DATA ({len(agents_data)} agents in system):"]
            for a in agents_data:
                avg   = round(a.get("computed_avg") or a.get("avg_score") or 0, 2)
                calls = a.get("call_count") or a.get("total_calls") or 0
                alrts = a.get("active_alerts") or 0
                team  = a.get("team") or "General"
                last  = a.get("last_call_at") or "never"
                lines.append(
                    f"  • {a['agent_id']} | Name: {a.get('name') or 'N/A'} | Team: {team} "
                    f"| Avg Score: {avg}/10 | Calls: {calls} | Active Alerts: {alrts} | Last Call: {last}"
                )

            lines.append(f"\nSYSTEM SUMMARY:")
            lines.append(f"  Total calls: {summary.get('total_calls', 0)}")
            lines.append(f"  Platform avg score: {summary.get('avg_score', 0)}/10")
            lines.append(f"  Platform avg F1: {round((summary.get('avg_f1') or 0)*100, 1)}%")
            lines.append(f"  Active alerts: {alert_sum.get('total_active', 0)} "
                         f"(critical: {alert_sum.get('critical_count', 0)})")

            agent_context = "\n".join(lines)
    except Exception:
        agent_context = "\n(Agent data temporarily unavailable)"

    # System prompt with full platform knowledge + live agent data
    system = f"""You are Criterion QA Assistant — an expert guide for the Criterion QA platform.

PLATFORM OVERVIEW:
Criterion QA is an AI-powered customer support quality auditor that analyzes audio calls and chat logs.

KEY FEATURES:
- Single Call Analysis: Upload MP3/M4A/WAV/TXT files for instant AI quality scoring
- Batch Processing: Analyze up to 500 files simultaneously in single or multi-agent mode
- Agent Dashboard: Track agent performance, compare agents, view score trends
- Alert Center: Real-time alerts for escalation, low scores, policy violations, unparliamentary language
- Policy Management: Upload company policy docs (TXT/PDF) indexed via RAG; every call checked against them
- History: Full call history with search, sentiment, F1 scores, compliance
- Logs: Real-time processing logs with level filtering

QUALITY METRICS (scored 1-10):
1. Empathy - emotional understanding and compassion
2. Communication - clarity and effectiveness
3. Resolution - problem-solving effectiveness
4. Compliance - adherence to company policy
5. Customer Satisfaction - predicted satisfaction
6. Professionalism - conduct and tone
7. F1 Score (0-100%) - harmonic mean of precision/recall

TECH STACK: Deepgram (transcription), OpenRouter/Claude/Mistral (scoring), SQLite/Supabase (storage)

ALERT TYPES: low_quality_score, low_empathy, low_compliance, sentiment_escalation, unparliamentary_language, policy_violation, processing_failure

HOW TO USE:
- Upload: drag-and-drop or click the upload zone on Analyze tab
- Batch: go to Batch tab → select mode → upload files → Start Batch
- Agents: click any agent card to see detailed trends and call history
- Policies: upload TXT/PDF in Policy tab; searches are automatic on each call
- Alerts: click dismiss to resolve; filter by severity/type
- History: search by filename, agent ID; click eye icon to view full results

TRANSCRIPTION: Files >10min are auto-chunked into 10-min segments, processed in parallel, then merged.
{agent_context}

When asked about agents, their performance, scores, or alerts — use the live data above to give accurate, specific answers.
Be helpful, concise, and specific. Answer questions about features, troubleshooting, and best practices."""

    payload = {
        "model": os.getenv("OPENROUTER_MODEL", "mistralai/mistral-7b-instruct"),
        "messages": [{"role": "system", "content": system}] + messages[-20:],
        "max_tokens": 600,
        "temperature": 0.7,
    }
    try:
        r = req.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=30,
        )
        r.raise_for_status()
        reply = r.json()["choices"][0]["message"]["content"]
        return ok({"reply": reply})
    except Exception as e:
        return err(f"Chatbot error: {str(e)}", 500)



# ─────────────────────────────────────────────────────────────
#  ANALYTICS
# ─────────────────────────────────────────────────────────────
@app.route("/api/analytics/summary")
def analytics():
    return ok(db.get_system_summary(
        from_date = request.args.get("from_date"),
        to_date   = request.args.get("to_date"),
    ))

# ─────────────────────────────────────────────────────────────
#  NOTIFICATIONS
# ─────────────────────────────────────────────────────────────
@app.route("/api/notifications/dual/agent", methods=["POST"])
def test_agent_channel():
    from notifications.dual_channel_notifier import get_dual_notifier
    r = get_dual_notifier().notify_agent(
        "low_quality_score","high","Test Agent Alert","This is a test QA alert",
        "test_agent","test_call",{"source":"manual_test"})
    return ok({"channel":"agent","result":r})

@app.route("/api/notifications/dual/system", methods=["POST"])
def test_system_channel():
    from notifications.dual_channel_notifier import get_dual_notifier
    r = get_dual_notifier().notify_system(
        "processing_failure","critical","Test System Alert","This is a test system/IT alert",
        "pipeline","test_call",{"source":"manual_test"})
    return ok({"channel":"system","result":r})

@app.route("/api/notifications/config")
def notif_config():
    from notifications.dual_channel_notifier import get_dual_notifier
    n = get_dual_notifier()
    return ok({
        "agent_channel":  {"slack":bool(n.agent_slack), "email":bool(n.agent_email and n.resend_key), "email_to":n.agent_email or "not set"},
        "system_channel": {"slack":bool(n.system_slack),"email":bool(n.system_email and n.resend_key),"email_to":n.system_email or "not set"},
        "llm_provider": os.getenv("LLM_PROVIDER","auto"),
        "model":        os.getenv("OPENROUTER_MODEL","not set"),
        "throttle_min": int(os.getenv("ALERT_THROTTLE_MINUTES","5")),
    })

@app.route("/api/notifications/test", methods=["POST"])
def notif_test():
    from notifications.notification_manager import get_notifier
    r = get_notifier().notify("test","high","Test Alert","Test from Criterion v3",
                              "test_agent","test_call",{"source":"manual"})
    return ok({"channels": r})



# ─────────────────────────────────────────────────────────────
#  LOGS API
# ─────────────────────────────────────────────────────────────
@app.route("/api/logs")
def get_logs():
    from pathlib import Path
    log_file = Path("/tmp/logs/criterion.log") if IS_VERCEL else Path(__file__).parent/"logs"/"criterion.log"
    if not log_file.exists():
        return ok({"logs":[], "message":"No logs yet"})
    lines = []
    try:
        with open(log_file,"r",encoding="utf-8") as f:
            raw = f.readlines()[-200:]  # last 200 lines
        for line in raw:
            try: lines.append(__import__('json').loads(line.strip()))
            except: lines.append({"msg":line.strip()})
    except Exception as e:
        return ok({"logs":[],"error":str(e)})
    level = request.args.get("level","").upper()
    if level:
        lines = [l for l in lines if l.get("level")==level]
    return ok({"logs":list(reversed(lines)),"total":len(lines)})

# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port  = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG","1") == "1"
    lp    = os.getenv("LLM_PROVIDER","auto")
    model = os.getenv("OPENROUTER_MODEL","not set")
    print(f"\n{'='*55}")
    print(f"  Criterion QA v3  →  http://localhost:{port}")
    print(f"  LLM Provider:  {lp}  ({model})")
    print(f"  Deepgram:      {'✓' if os.getenv('DEEPGRAM_API_KEY') else '✗ MISSING'}")
    print(f"  Anthropic:     {'✓' if os.getenv('ANTHROPIC_API_KEY') else '○ not set'}")
    print(f"  OpenRouter:    {'✓' if os.getenv('OPENROUTER_API_KEY') else '○ not set'}")
    print(f"  Mistral:       {'✓' if os.getenv('MISTRAL_API_KEY') else '○ not set'}")
    print(f"  Batch workers: {os.getenv('BATCH_WORKERS',20)}")
    print(f"{'='*55}\n")
    app.run(host="0.0.0.0", port=port, debug=debug)
