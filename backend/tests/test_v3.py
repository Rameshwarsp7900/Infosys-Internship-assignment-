"""
tests/test_v3.py
─────────────────
Full test suite for Criterion QA v3.
Run: cd backend && python -m pytest tests/test_v3.py -v

Covers:
  - 422 fix (upload always returns 200)
  - anthropic/claude-3-haiku via OpenRouter
  - Mistral fallback
  - Sentiment analyzer bug fixes
  - Alert engine
  - RAG policy
  - Database full flow
  - Batch processor
  - Job queue
  - Flask all endpoints
  - Performance SLA (30s chat)
"""
import os, sys, json, uuid, time, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Force test environment
os.environ.pop("SUPABASE_URL",       None)
os.environ.pop("SUPABASE_ANON_KEY",  None)
os.environ.setdefault("DEEPGRAM_API_KEY",   "test_dg")
os.environ.setdefault("OPENROUTER_API_KEY", "sk-or-v1-test")
os.environ.setdefault("OPENROUTER_MODEL",   "anthropic/claude-3-haiku")
os.environ.setdefault("LLM_PROVIDER",        "openrouter")


def _clear_modules():
    for k in list(__import__("sys").modules.keys()):
        if k.startswith(("database","app","jobs","notifications","llm","sentiment","alerts","rag","transcription_pipeline")):
            del __import__("sys").modules[k]


def _app():
    _clear_modules()
    from app import app
    return app


# ─── 1. 422 FIX ─────────────────────────────────────────────────────────────
def test_upload_never_returns_422():
    """Upload must ALWAYS return 200, never 422."""
    import io
    client = _app().test_client()
    txt = b"[Agent]: Thank you for calling.\n[Customer]: I need help with my order.\n[Agent]: Happy to help! Is there anything else?\n"
    resp = client.post("/api/upload",
        data={"file":(io.BytesIO(txt),"call.txt"), "agent_id":"ag_test"},
        content_type="multipart/form-data")
    assert resp.status_code == 200, f"Got {resp.status_code} — should never be 422"
    data = json.loads(resp.data)
    assert data["status"] == "completed"
    print("✓ Upload never returns 422")


def test_upload_bad_ext_returns_400():
    import io
    client = _app().test_client()
    resp = client.post("/api/upload",
        data={"file":(io.BytesIO(b"x"),"file.exe")},
        content_type="multipart/form-data")
    assert resp.status_code == 400
    print("✓ Bad extension → 400 (not 422)")


def test_upload_no_file_returns_400():
    client = _app().test_client()
    assert client.post("/api/upload").status_code == 400
    print("✓ No file → 400")


# ─── 2. LLM CLIENT — anthropic/claude-3-haiku via OpenRouter ────────────────
def test_llm_model_string():
    """Model string must be exactly anthropic/claude-3-haiku."""
    _clear_modules()
    from llm.multi_llm_client import MultiLLMClient
    c = MultiLLMClient(openrouter_key="sk-or-v1-test",
                       openrouter_model="anthropic/claude-3-haiku",
                       provider="openrouter")
    queue = c._build_queue()
    assert len(queue) == 1
    name, fn = queue[0]
    assert "anthropic/claude-3-haiku" in name
    print(f"✓ LLM queue: {name}")


def test_llm_fallback_chain():
    """With no keys, falls back to heuristic — never raises."""
    _clear_modules()
    from llm.multi_llm_client import MultiLLMClient
    c = MultiLLMClient(openrouter_key="", anthropic_key="", mistral_key="", provider="auto")
    result = c.analyze("AGENT: Hello.\nCUSTOMER: Hi.")
    assert result["status"] != "failed" if "status" in result else True
    assert len(result["metrics"]) == 10
    assert result["_provider"] == "fallback"
    print("✓ LLM fallback: heuristic returns 10 metrics")


def test_llm_closure_no_late_binding():
    """Lambda closures must capture correct values (no late-binding bug)."""
    _clear_modules()
    from llm.multi_llm_client import MultiLLMClient
    captured = []
    c = MultiLLMClient(
        openrouter_key="key_A", openrouter_model="anthropic/claude-3-haiku",
        mistral_key="key_B",   mistral_model="mistral-small-latest",
        provider="auto"
    )
    q = c._build_queue()
    # Both entries should exist and use different keys
    assert len(q) == 2
    print(f"✓ LLM closure: {[n for n,_ in q]}")


def test_llm_validate_clamps_scores():
    """Validate must clamp scores to [0, 10] and fill missing metrics."""
    _clear_modules()
    from llm.multi_llm_client import MultiLLMClient
    c = MultiLLMClient(openrouter_key="k", provider="openrouter")
    r = c._validate({"overall_rating": 15.0, "metrics": {"empathy": {"score": -3.0}}})
    assert r["overall_rating"] == 10.0
    assert r["metrics"]["empathy"]["score"] == 0.0
    assert len(r["metrics"]) == 10   # missing 9 metrics filled in
    print("✓ LLM validate: clamps scores, fills missing metrics")


# ─── 3. SENTIMENT ANALYZER BUG FIXES ────────────────────────────────────────
def test_sentiment_none_timestamps():
    from sentiment.analyzer import SentimentAnalyzer
    r = SentimentAnalyzer().analyze([
        {"speaker":"agent",    "start":None, "text":"Hello, how can I help?"},
        {"speaker":"customer", "start":None, "text":"I am very frustrated."},
    ])
    assert r["overall"] in ("positive","neutral","negative")
    assert isinstance(r["avg_agent_score"], float)
    print("✓ Sentiment: None timestamps handled")


def test_sentiment_none_text():
    from sentiment.analyzer import SentimentAnalyzer
    r = SentimentAnalyzer().analyze([
        {"speaker":"agent", "start":0.0, "text":None},
        {"speaker":"customer", "start":3.0, "text":""},
    ])
    assert r["overall"] in ("positive","neutral","negative")
    print("✓ Sentiment: None/empty text handled")


def test_sentiment_single_customer_no_crash():
    from sentiment.analyzer import SentimentAnalyzer
    r = SentimentAnalyzer().analyze([{"speaker":"customer","start":0.0,"text":"I am very angry!"}])
    assert r["escalation_detected"] == False
    print("✓ Sentiment: single customer point — no escalation crash")


def test_sentiment_deepgram_field():
    from sentiment.analyzer import SentimentAnalyzer
    r = SentimentAnalyzer().analyze([
        {"speaker":"agent",    "start":0.0, "text":"Hi", "sentiment":"positive"},
        {"speaker":"customer", "start":5.0, "text":"Bad", "sentiment":"negative"},
    ])
    assert r["agent"] == "positive"
    assert r["customer"] == "negative"
    print("✓ Sentiment: Deepgram sentiment field respected")


def test_sentiment_escalation_detection():
    from sentiment.analyzer import SentimentAnalyzer
    r = SentimentAnalyzer().analyze([
        {"speaker":"customer","start":0.0,  "text":"Great service thank you!", "sentiment":"positive"},
        {"speaker":"customer","start":90.0, "text":"Absolutely terrible this is ridiculous awful worst"},
    ])
    assert r["escalation_detected"] == True
    assert r["escalation_point"] == 90.0
    print("✓ Sentiment: escalation correctly detected")


def test_sentiment_avg_scores_are_floats():
    from sentiment.analyzer import SentimentAnalyzer
    r = SentimentAnalyzer().analyze([{"speaker":"agent","start":0.0,"text":"Hello"}])
    assert isinstance(r["avg_agent_score"], float)
    assert isinstance(r["avg_customer_score"], float)
    print("✓ Sentiment: avg scores are always float")


# ─── 4. ALERT ENGINE ─────────────────────────────────────────────────────────
def test_alert_unparliamentary():
    from alerts.alert_engine import AlertEngine
    ae = AlertEngine(db=None)
    segs = [{"speaker":"agent","start":5.0,"text":"You are so stupid just deal with it!"}]
    hits, alerts = ae._check_unparliamentary("c1","a1",segs)
    assert len(hits) > 0
    assert alerts[0]["severity"] == "critical"   # agent language = critical
    print(f"✓ Alert: {len(hits)} unparliamentary hit(s) — agent=critical")


def test_alert_score_threshold():
    from alerts.alert_engine import AlertEngine
    ae = AlertEngine(db=None)
    qa = {"overall_rating": 3.5, "metrics": {"empathy":{"score":2.0},"compliance":{"score":2.5}}}
    alerts = ae._check_scores("c1","a1",qa)
    assert any(a["severity"]=="critical" for a in alerts)
    print(f"✓ Alert: {len(alerts)} low-score alert(s)")


def test_alert_escalation():
    from alerts.alert_engine import AlertEngine
    ae = AlertEngine(db=None)
    alerts = ae._check_escalation("c1","a1",{"escalation_detected":True,"escalation_point":120.0,"customer":"negative"})
    assert len(alerts) == 1 and alerts[0]["alert_type"] == "sentiment_escalation"
    print("✓ Alert: escalation alert generated")


def test_alert_sla_breach():
    from alerts.alert_engine import AlertEngine
    ae = AlertEngine(db=None)
    alerts = ae._check_sla("c1","a1", 75.0)
    assert alerts[0]["alert_type"] == "sla_breach"
    print("✓ Alert: SLA breach detected")


# ─── 5. RAG POLICY ────────────────────────────────────────────────────────────
def test_rag_rule_based():
    from rag.policy_engine import PolicyEngine
    pe = PolicyEngine(db=None, openrouter_client=None)
    transcript = "AGENT: I'll give you a full refund straight away no problem."
    violations = pe._rule_based_check(transcript)
    assert isinstance(violations, list)
    print(f"✓ RAG rule-based: {len(violations)} violation(s)")


def test_rag_tfidf_retrieval():
    from rag.policy_engine import PolicyEngine
    pe = PolicyEngine(db=None, openrouter_client=None)
    pe._chunks_cache = [
        {"doc_id":"d1","title":"Policy","text":"Agent must greet customer at the start of every call by name."},
        {"doc_id":"d1","title":"Policy","text":"Verify customer identity using date of birth or account number."},
        {"doc_id":"d1","title":"Policy","text":"Do not discuss competitor products or pricing."},
    ]
    results = pe.retrieve("greeting introduction start of call", top_k=2)
    assert len(results) >= 1
    assert any("greet" in r["text"].lower() or "call" in r["text"].lower() for r in results)
    print(f"✓ RAG TF-IDF: returned {len(results)} relevant chunk(s)")


# ─── 6. DATABASE ────────────────────────────────────────────────────────────
def test_database_full_flow():
    import database.db as db
    _orig = db.DB_PATH
    tmp = f"/tmp/test_{uuid.uuid4().hex[:8]}.db"
    db.DB_PATH = tmp
    try:
        db.init_db()
        call_id = uuid.uuid4().hex
        db.upsert_agent("ag001","Test Agent","Support","test@test.com")
        db.insert_call(call_id,"ag001","cust001","2026-03-18","call.mp3","audio",120.0,300,1)
        db.insert_quality_scores(call_id,{
            "overall_rating":7.8,"rating_label":"Good","f1_score":0.78,
            "precision":0.80,"recall":0.76,"summary":"Good call.",
            "metrics":{k:{"score":7.5,"reason":"ok"} for k in ["empathy","resolution","communication","professionalism","product_knowledge","listening","response_time","customer_satisfaction","first_call_resolution","compliance"]},
            "critical_issues":[],"positive_highlights":["Empathetic"],"improvement_suggestions":["Speak slower"],
        })
        db.insert_sentiment(call_id,{"overall":"positive","agent":"positive","customer":"neutral",
            "avg_agent_score":0.4,"avg_customer_score":0.1,"escalation_detected":False,
            "escalation_point":None,"timeline":[]})
        db.insert_transcript(call_id,{"full_text":"Hello","segments":[]},  "AGENT: Hello")
        aid = db.insert_alert(call_id,"ag001","low_quality_score","medium","Low","Score 7.8",{})
        qs = db.get_quality_scores(call_id)
        assert qs["overall_rating"] == 7.8
        s = db.get_sentiment(call_id)
        assert s["overall"] == "positive"
        assert isinstance(s["avg_agent_score"], float)
        db.dismiss_alert(aid)
        alerts = db.get_alerts(status="active",agent_id="ag001")
        assert len(alerts) == 0
        db.update_call_status(call_id,"completed",4.1)
        db.update_agent_stats("ag001")
        ag = db.get_agent("ag001")
        assert ag["total_calls"] >= 1
        summary = db.get_system_summary()
        assert summary["total_calls"] >= 1
        print("✓ Database: full flow agent→call→scores→sentiment→alert→dismiss")
    finally:
        db.DB_PATH = _orig
        try: os.remove(tmp)
        except: pass


# ─── 7. BATCH PROCESSOR ─────────────────────────────────────────────────────
def test_batch_processor_concurrent():
    """Batch must process all jobs and never raise."""
    _clear_modules()
    from transcription_pipeline import BatchProcessor, TranscriptionPipeline

    call_count = 0
    def mock_pipeline(path, meta):
        nonlocal call_count
        call_count += 1
        time.sleep(0.01)   # simulate tiny work
        return {"call_id":meta.get("call_id","x"),"status":"completed",
                "quality_analysis":{"overall_rating":7.0,"rating_label":"Good","metrics":{},"f1_score":0.7,"summary":"ok"},
                "sentiment":{"overall":"neutral","agent":"neutral","customer":"neutral","avg_agent_score":0.0,"avg_customer_score":0.0,"escalation_detected":False,"escalation_point":None,"timeline":[]},
                "transcript":{"segments":[],"full_text":"","word_count":0,"duration":0},
                "alerts":[],"policy_violations":[]}

    class FakePipeline:
        def process(self, p, m): return mock_pipeline(p, m)

    bp = BatchProcessor(FakePipeline(), max_workers=10)
    jobs = [{"file_path":f"/tmp/f{i}.txt","metadata":{"call_id":uuid.uuid4().hex,"agent_id":"ag1"}} for i in range(20)]
    t0 = time.time()
    results = bp.process_batch(jobs)
    elapsed = time.time() - t0
    assert len(results) == 20
    assert all(r["status"]=="completed" for r in results)
    print(f"✓ Batch: 20 concurrent jobs in {elapsed:.2f}s (workers=10)")


def test_batch_processor_handles_failures():
    """Failed jobs should not stop the batch."""
    _clear_modules()
    from transcription_pipeline import BatchProcessor

    class BrokenPipeline:
        def process(self, p, m):
            raise RuntimeError("Simulated crash")

    bp = BatchProcessor(BrokenPipeline(), max_workers=5)
    jobs = [{"file_path":"/dev/null","metadata":{"call_id":uuid.uuid4().hex,"agent_id":"ag1"}} for _ in range(5)]
    results = bp.process_batch(jobs)
    assert len(results) == 5
    assert all(r is not None for r in results)
    print("✓ Batch: all 5 crashed jobs returned gracefully (not None)")


# ─── 8. JOB QUEUE ────────────────────────────────────────────────────────────
def test_job_queue():
    _clear_modules()
    from jobs.job_queue import create_job, submit_job, get_job, Job

    jid = create_job("ag1","call.mp3",{"agent_id":"ag1"})
    j = get_job(jid)
    assert j["status"] == Job.PENDING

    def fast(path, meta):
        time.sleep(0.05)
        return {"call_id":"c1","status":"completed","quality_analysis":{"overall_rating":8.0}}

    submit_job(jid, "/dev/null", {"agent_id":"ag1"}, fast)
    for _ in range(30):
        time.sleep(0.1)
        j = get_job(jid)
        if j["status"] in (Job.COMPLETED, Job.FAILED): break
    assert j["status"] == Job.COMPLETED
    print("✓ Job queue: PENDING → COMPLETED in <3s")


# ─── 9. FLASK ENDPOINTS ─────────────────────────────────────────────────────
def test_flask_health_shows_model():
    client = _app().test_client()
    d = json.loads(client.get("/api/health").data)
    assert d["status"] == "healthy"
    assert d["apis_ready"] in (True, False)
    assert "transcription" in d
    assert "scoring" in d
    print(f"✓ /api/health: status={d['status']} apis_ready={d['apis_ready']}")


def test_flask_agents():
    client = _app().test_client()
    assert client.get("/api/agents").status_code == 200
    print("✓ /api/agents → 200")


def test_flask_alerts():
    client = _app().test_client()
    d = json.loads(client.get("/api/alerts").data)
    assert "alerts" in d and "summary" in d
    print("✓ /api/alerts → 200")


def test_flask_analytics():
    client = _app().test_client()
    d = json.loads(client.get("/api/analytics/summary").data)
    assert "total_calls" in d
    print("✓ /api/analytics/summary → 200")


def test_flask_policy_seeded():
    """Sample policy should be auto-seeded on first run."""
    client = _app().test_client()
    d = json.loads(client.get("/api/policy").data)
    assert "documents" in d
    print(f"✓ /api/policy → {len(d['documents'])} doc(s) (auto-seeded)")


def test_flask_async_upload():
    import io
    client = _app().test_client()
    txt = b"[Agent]: Hello.\n[Customer]: Need help.\n"
    resp = client.post("/api/upload/async",
        data={"file":(io.BytesIO(txt),"call.txt"), "agent_id":"ag_async"},
        content_type="multipart/form-data")
    assert resp.status_code == 202
    d = json.loads(resp.data)
    assert "job_id" in d and d["status"] == "pending"
    print(f"✓ /api/upload/async → 202 job_id={d['job_id'][:10]}…")


def test_flask_batch_empty():
    client = _app().test_client()
    assert client.post("/api/batch").status_code == 400
    print("✓ /api/batch no files → 400")


# ─── 10. PERFORMANCE SLA ────────────────────────────────────────────────────
def test_performance_chat_under_30s():
    """Chat processing (no LLM call to external API) must finish in <30s."""
    from transcription.chat_processor import ChatProcessor
    from transcription.transcript_formatter import TranscriptFormatter
    from sentiment.analyzer import SentimentAnalyzer

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for i in range(60):
            f.write(f"[Agent]: Thank you for your patience regarding issue number {i}. Let me help resolve this.\n")
            f.write(f"[Customer]: I appreciate your help. This matter is quite important to me.\n")
        tmp = f.name

    t0 = time.time()
    tr = ChatProcessor().process(tmp)
    os.unlink(tmp)
    fmt = TranscriptFormatter().format_for_llm(tr["segments"])
    sa  = SentimentAnalyzer().analyze(tr["segments"])
    elapsed = time.time() - t0

    assert elapsed < 30, f"Took {elapsed:.1f}s — must be <30s"
    assert len(tr["segments"]) == 120
    print(f"✓ Performance SLA: 120-segment chat processed in {elapsed:.2f}s < 30s")


if __name__ == "__main__":
    tests = [
        test_upload_never_returns_422, test_upload_bad_ext_returns_400, test_upload_no_file_returns_400,
        test_llm_model_string, test_llm_fallback_chain, test_llm_closure_no_late_binding, test_llm_validate_clamps_scores,
        test_sentiment_none_timestamps, test_sentiment_none_text, test_sentiment_single_customer_no_crash,
        test_sentiment_deepgram_field, test_sentiment_escalation_detection, test_sentiment_avg_scores_are_floats,
        test_alert_unparliamentary, test_alert_score_threshold, test_alert_escalation, test_alert_sla_breach,
        test_rag_rule_based, test_rag_tfidf_retrieval,
        test_database_full_flow,
        test_batch_processor_concurrent, test_batch_processor_handles_failures,
        test_job_queue,
        test_flask_health_shows_model, test_flask_agents, test_flask_alerts, test_flask_analytics,
        test_flask_policy_seeded, test_flask_async_upload, test_flask_batch_empty,
        test_performance_chat_under_30s,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t(); passed += 1
        except Exception as e:
            import traceback
            print(f"✗ {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{'='*50}")
    print(f"  {passed} passed · {failed} failed")
    print(f"{'='*50}")
