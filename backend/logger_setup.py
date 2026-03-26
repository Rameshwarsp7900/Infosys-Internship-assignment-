"""
backend/logger_setup.py — v3 fixed
────────────────────────────────────
Key fix: Python's LogRecord has reserved instance attributes that CANNOT appear
in extra={}: filename, module, lineno, funcName, name, msg, args, created,
levelname, levelno, pathname, process, processName, relativeCreated, thread,
threadName, exc_info, exc_text, stack_info, msecs, taskName, message, asctime.

If ANY of these appear as keys in extra={}, Python raises:
  KeyError: "Attempt to overwrite 'filename' in LogRecord"

This propagated up the call stack → caught by LLM try/except →
stored as the quality analysis summary text (the bug shown in screenshots).

Fix: JSONFormatter and all log_* helpers use safe key names only.
     JSONFormatter also filters reserved names when iterating record.__dict__.
"""

import os, json, logging, logging.handlers
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR   = Path(os.getenv("LOG_DIR", "/tmp/logs" if os.getenv("VERCEL") else str(Path(__file__).parent / "logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ── Reserved LogRecord attribute names that CANNOT be in extra={} ─────────────
_RESERVED = frozenset({
    "name","msg","args","created","filename","funcName","levelname","levelno",
    "lineno","module","msecs","pathname","process","processName","relativeCreated",
    "stack_info","thread","threadName","exc_info","exc_text","taskName",
    "message","asctime",
})

def _safe_extra(d: dict) -> dict:
    """Strip any reserved LogRecord keys from an extra dict before logging."""
    return {k: v for k, v in d.items() if k not in _RESERVED}


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts":      datetime.now(timezone.utc).isoformat(),
            "level":   record.levelname,
            "logger":  record.name,
            "msg":     record.getMessage(),
            "module":  record.module,
            "line":    record.lineno,
        }
        # Only add non-reserved, JSON-serialisable extra fields
        for key, val in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            try:
                json.dumps(val)
                payload[key] = val
            except (TypeError, ValueError):
                payload[key] = str(val)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _build_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    logger.propagate = False
    fmt = JSONFormatter()
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    try:
        fh = logging.handlers.RotatingFileHandler(
            LOG_DIR / "criterion.log", maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        eh = logging.handlers.RotatingFileHandler(
            LOG_DIR / "criterion_errors.log", maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
        eh.setLevel(logging.ERROR)
        eh.setFormatter(fmt)
        logger.addHandler(eh)
    except Exception:
        pass
    return logger

_loggers: dict = {}
def get_logger(name: str) -> logging.Logger:
    if name not in _loggers:
        _loggers[name] = _build_logger(name)
    return _loggers[name]

_event_log = get_logger("criterion.events")

# ── All helpers use safe key names (no "filename", no reserved names) ─────────

def log_pipeline_start(call_id: str, agent_id: str, audio_file: str, file_type: str):
    """Use 'audio_file' not 'filename' — 'filename' is a reserved LogRecord attr."""
    _event_log.info("Pipeline started", extra=_safe_extra({
        "event":"pipeline_start","call_id":call_id,
        "agent_id":agent_id,"audio_file":audio_file,"file_type":file_type,
    }))

def log_transcription_result(call_id: str, word_count: int, segments: int, duration: float, provider: str="deepgram"):
    _event_log.info("Transcription complete", extra=_safe_extra({
        "event":"transcription_done","call_id":call_id,
        "word_count":word_count,"segment_count":segments,
        "duration_s":duration,"provider":provider,
    }))

def log_transcription_error(call_id: str, error: str):
    _event_log.error("Transcription failed", extra=_safe_extra({
        "event":"transcription_error","call_id":call_id,"error":error,
    }))

def log_llm_result(call_id: str, provider: str, score: float, latency_ms: int):
    _event_log.info("LLM scoring complete", extra=_safe_extra({
        "event":"llm_done","call_id":call_id,
        "provider":provider,"overall_score":score,"latency_ms":latency_ms,
    }))

def log_llm_error(call_id: str, provider: str, error: str):
    _event_log.error("LLM provider failed", extra=_safe_extra({
        "event":"llm_error","call_id":call_id,"provider":provider,"error":error,
    }))

def log_alert_fired(call_id: str, agent_id: str, alert_type: str, severity: str, channel: str):
    _event_log.warning("Alert fired", extra=_safe_extra({
        "event":"alert_fired","call_id":call_id,"agent_id":agent_id,
        "alert_type":alert_type,"severity":severity,"channel":channel,
    }))

def log_system_error(component: str, error: str, call_id: str=None):
    _event_log.error("System error", extra=_safe_extra({
        "event":"system_error","component":component,"error":error,
        "call_id":call_id or "system",
    }))

def log_pipeline_complete(call_id: str, agent_id: str, score: float, elapsed_s: float, warnings: list):
    _event_log.info("Pipeline complete", extra=_safe_extra({
        "event":"pipeline_complete","call_id":call_id,"agent_id":agent_id,
        "overall_score":score,"elapsed_s":elapsed_s,"warnings_count":len(warnings),
    }))

def log_api_request(method: str, path: str, status: int, elapsed_ms: int):
    _event_log.info("API request", extra=_safe_extra({
        "event":"api_request","method":method,"path":path,
        "status":status,"elapsed_ms":elapsed_ms,
    }))
