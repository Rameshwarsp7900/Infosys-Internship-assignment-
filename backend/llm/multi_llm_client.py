"""
backend/llm/multi_llm_client.py
────────────────────────────────
Resilient multi-provider LLM client.

Priority chain:
  1. anthropic/claude-3-haiku (direct Anthropic API — most reliable)
  2. OpenRouter (OPENROUTER_MODEL, default llama-3.1-8b:free)
  3. Mistral API  (MISTRAL_API_KEY, mistral-small-latest — free 1M tokens/month)
  4. Built-in heuristic fallback (never fails, returns sensible defaults)

Set in .env:
  LLM_PROVIDER=claude|openrouter|mistral|auto   (default: auto)
  ANTHROPIC_API_KEY=sk-ant-...
  OPENROUTER_API_KEY=sk-or-v1-...
  OPENROUTER_MODEL=anthropic/claude-3-haiku      ← use this!
  MISTRAL_API_KEY=...
  MISTRAL_MODEL=mistral-small-latest
"""

import os, json, re, time, logging
from typing import Optional
import requests

logger = logging.getLogger(__name__)

# ── Endpoints ──────────────────────────────────────────────────
ANTHROPIC_URL  = "https://api.anthropic.com/v1/messages"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MISTRAL_URL    = "https://api.mistral.ai/v1/chat/completions"

# ── System prompt (shared) ─────────────────────────────────────
SYSTEM_PROMPT = """You are a senior customer support quality analyst.
Evaluate the call/chat transcript on 10 metrics. Reply ONLY with valid JSON — no markdown, no preamble.

{
  "overall_rating": 7.5,
  "rating_label": "Good",
  "metrics": {
    "empathy":               {"score": 8.0, "reason": "one sentence"},
    "resolution":            {"score": 7.5, "reason": "one sentence"},
    "communication":         {"score": 8.0, "reason": "one sentence"},
    "professionalism":       {"score": 8.5, "reason": "one sentence"},
    "product_knowledge":     {"score": 7.0, "reason": "one sentence"},
    "listening":             {"score": 8.0, "reason": "one sentence"},
    "response_time":         {"score": 7.5, "reason": "one sentence"},
    "customer_satisfaction": {"score": 8.0, "reason": "one sentence"},
    "first_call_resolution": {"score": 7.5, "reason": "one sentence"},
    "compliance":            {"score": 8.0, "reason": "one sentence"}
  },
  "sentiment": {"overall": "positive", "agent": "positive", "customer": "neutral"},
  "f1_score": 0.75,
  "precision": 0.78,
  "recall": 0.72,
  "critical_issues": ["issue if any"],
  "positive_highlights": ["highlight"],
  "improvement_suggestions": ["suggestion"],
  "summary": "2-3 sentence executive summary"
}

Rating scale: 9-10=Excellent, 7-8.9=Good, 5-6.9=Average, 0-4.9=Poor"""


def _clean_json(raw: str) -> dict:
    """Strip markdown fences and parse JSON robustly."""
    clean = re.sub(r"```[a-z]*\n?", "", raw).strip().rstrip("```").strip()
    # Find first { and last }
    start = clean.find("{")
    end   = clean.rfind("}")
    if start != -1 and end != -1:
        clean = clean[start:end+1]
    return json.loads(clean)


def _fallback_response(note: str = "") -> dict:
    """Returns a safe default when all LLM providers fail."""
    m = {k: {"score": 5.0, "reason": "Could not score — LLM unavailable"} for k in [
        "empathy","resolution","communication","professionalism","product_knowledge",
        "listening","response_time","customer_satisfaction","first_call_resolution","compliance"
    ]}
    return {
        "overall_rating": 5.0, "rating_label": "Average",
        "metrics": m,
        "sentiment": {"overall":"neutral","agent":"neutral","customer":"neutral"},
        "f1_score": 0.5, "precision": 0.5, "recall": 0.5,
        "critical_issues": [], "positive_highlights": [],
        "improvement_suggestions": ["Enable LLM API key for detailed analysis"],
        "summary": f"LLM scoring unavailable. {note}".strip(),
        "_provider": "fallback",
    }


# ─────────────────────────────────────────────────────────────
#  Provider 1: Anthropic (claude-3-haiku)
# ─────────────────────────────────────────────────────────────
def _call_anthropic(transcript: str, api_key: str) -> dict:
    resp = requests.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        json={
            "model":      "claude-haiku-4-5",
            "max_tokens": 1500,
            "system":     SYSTEM_PROMPT,
            "messages":   [{"role":"user","content":f"TRANSCRIPT:\n{transcript[:4500]}"}],
        },
        timeout=55,
    )
    resp.raise_for_status()
    content = resp.json()["content"][0]["text"]
    result  = _clean_json(content)
    result["_provider"] = "claude-haiku-4-5"
    return result


# ─────────────────────────────────────────────────────────────
#  Provider 2: OpenRouter
# ─────────────────────────────────────────────────────────────
def _call_openrouter(transcript: str, api_key: str, model: str) -> dict:
    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
            "HTTP-Referer":  "https://criterion-qa.vercel.app",
        },
        json={
            "model":      model,
            "messages":   [
                {"role":"system","content":SYSTEM_PROMPT},
                {"role":"user",  "content":f"TRANSCRIPT:\n{transcript[:4500]}"},
            ],
            "temperature":     0.2,
            "max_tokens":      1500,
            "response_format": {"type":"json_object"},
        },
        timeout=55,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    result  = _clean_json(content)
    result["_provider"] = f"openrouter/{model}"
    return result


# ─────────────────────────────────────────────────────────────
#  Provider 3: Mistral
# ─────────────────────────────────────────────────────────────
def _call_mistral(transcript: str, api_key: str, model: str) -> dict:
    resp = requests.post(
        MISTRAL_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
        json={
            "model":        model,
            "messages":     [
                {"role":"system","content":SYSTEM_PROMPT},
                {"role":"user",  "content":f"TRANSCRIPT:\n{transcript[:4500]}"},
            ],
            "temperature":  0.2,
            "max_tokens":   1500,
            "response_format": {"type":"json_object"},
        },
        timeout=55,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    result  = _clean_json(content)
    result["_provider"] = f"mistral/{model}"
    return result


# ─────────────────────────────────────────────────────────────
#  Main Client
# ─────────────────────────────────────────────────────────────
class MultiLLMClient:
    """
    Tries providers in order until one succeeds.
    Never raises — always returns a result dict.
    """

    def __init__(
        self,
        anthropic_key:  Optional[str] = None,
        openrouter_key: Optional[str] = None,
        openrouter_model: Optional[str] = None,
        mistral_key:    Optional[str] = None,
        mistral_model:  Optional[str] = None,
        provider:       Optional[str] = None,  # force: claude|openrouter|mistral|auto
    ):
        self.anthropic_key    = anthropic_key    or os.getenv("ANTHROPIC_API_KEY",   "")
        self.openrouter_key   = openrouter_key   or os.getenv("OPENROUTER_API_KEY",  "")
        self.openrouter_model = openrouter_model or os.getenv("OPENROUTER_MODEL",    "anthropic/claude-3-haiku")
        self.mistral_key      = mistral_key      or os.getenv("MISTRAL_API_KEY",     "")
        self.mistral_model    = mistral_model    or os.getenv("MISTRAL_MODEL",       "mistral-small-latest")
        self.provider         = provider         or os.getenv("LLM_PROVIDER",        "auto")

    def analyze(self, transcript_text: str, metadata: dict = None) -> dict:
        """Score a transcript. Tries all configured providers, never raises."""
        if not transcript_text or not transcript_text.strip():
            return _fallback_response("Empty transcript received")

        metadata = metadata or {}
        errors   = []

        # Build provider queue
        queue = self._build_queue()

        for provider_name, fn in queue:
            try:
                logger.info(f"[LLM] Trying provider: {provider_name}")
                t0     = time.time()
                result = fn(transcript_text)
                result["_latency_ms"] = round((time.time()-t0)*1000)
                logger.info(f"[LLM] {provider_name} succeeded in {result['_latency_ms']}ms")
                return self._validate(result)
            except Exception as e:
                err = f"{provider_name}: {str(e)[:120]}"
                errors.append(err)
                logger.warning(f"[LLM] Provider failed — {err}")
                time.sleep(1)   # brief back-off before next provider

        logger.error(f"[LLM] All providers failed: {errors}")
        fb = _fallback_response(f"All providers failed: {'; '.join(errors)}")
        fb["_errors"] = errors
        return fb

    def _build_queue(self):
        """
        Build ordered provider queue.
        Default order: OpenRouter → Anthropic direct → Mistral → fallback.
        When LLM_PROVIDER=openrouter, OpenRouter goes first (uses anthropic/claude-3-haiku).
        """
        queue = []

        # Capture values NOW to avoid late-binding closure bugs in lambdas
        or_key   = self.openrouter_key
        or_model = self.openrouter_model
        an_key   = self.anthropic_key
        mi_key   = self.mistral_key
        mi_model = self.mistral_model

        prov = self.provider

        if prov in ("openrouter", "auto"):
            if or_key:
                queue.append((
                    f"openrouter/{or_model}",
                    lambda t, k=or_key, m=or_model: _call_openrouter(t, k, m)
                ))

        if prov in ("claude", "auto"):
            if an_key:
                queue.append((
                    "anthropic/claude-haiku-direct",
                    lambda t, k=an_key: _call_anthropic(t, k)
                ))

        if prov in ("mistral", "auto"):
            if mi_key:
                queue.append((
                    f"mistral/{mi_model}",
                    lambda t, k=mi_key, m=mi_model: _call_mistral(t, k, m)
                ))

        if not queue:
            logger.warning("[LLM] No API keys configured — will use heuristic fallback")

        return queue

    @staticmethod
    def _validate(result: dict) -> dict:
        """Ensure all required keys exist with valid values."""
        metrics_keys = [
            "empathy","resolution","communication","professionalism","product_knowledge",
            "listening","response_time","customer_satisfaction","first_call_resolution","compliance"
        ]
        if "metrics" not in result or not isinstance(result["metrics"], dict):
            result["metrics"] = {}
        for k in metrics_keys:
            if k not in result["metrics"]:
                result["metrics"][k] = {"score": 5.0, "reason": "Not scored"}
            m = result["metrics"][k]
            if not isinstance(m.get("score"), (int, float)):
                m["score"] = 5.0
            m["score"] = max(0.0, min(10.0, float(m["score"])))

        result.setdefault("overall_rating",   sum(v["score"] for v in result["metrics"].values()) / 10)
        result["overall_rating"] = max(0.0, min(10.0, float(result["overall_rating"])))

        score = result["overall_rating"]
        result.setdefault("rating_label",
            "Excellent" if score>=9 else "Good" if score>=7 else "Average" if score>=5 else "Poor")

        result.setdefault("f1_score",   0.5)
        result.setdefault("precision",  0.5)
        result.setdefault("recall",     0.5)
        result.setdefault("summary",    "Analysis complete.")
        result.setdefault("sentiment",  {"overall":"neutral","agent":"neutral","customer":"neutral"})
        result.setdefault("critical_issues",          [])
        result.setdefault("positive_highlights",      [])
        result.setdefault("improvement_suggestions",  [])

        return result


# ── Backwards-compatible alias so v2 imports still work ───────
class OpenRouterClient(MultiLLMClient):
    def __init__(self, api_key=None, model=None):
        super().__init__(openrouter_key=api_key, openrouter_model=model)
