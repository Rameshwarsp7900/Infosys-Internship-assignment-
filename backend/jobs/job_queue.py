"""
jobs/job_queue.py
──────────────────
In-process background job queue for long-running call analysis.

Why not Redis/Celery?
  • Vercel free tier: no persistent workers, 60s function timeout
  • Redis (Upstash free): 10k commands/day — enough for light use, but adds
    complexity for a submission project
  • This solution: Python threading + in-memory dict. Works on any server.
    On Vercel, use it for files <10 min (fit in 60s). For >30 min, return
    a job_id and the client polls /api/jobs/{id}.

Job lifecycle:
  pending → running → completed | failed

API:
  POST /api/upload?async=1   → returns {job_id, status: "pending"}
  GET  /api/jobs/{job_id}    → returns {job_id, status, result?, progress?}
  GET  /api/jobs             → returns all recent jobs (last 100)

Thread safety: all dict mutations guarded by threading.Lock().
"""

import threading
import time
import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Callable

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
#  Job store
# ─────────────────────────────────────────────────────────────
_jobs: Dict[str, Dict] = {}
_lock = threading.Lock()

MAX_JOBS    = 200   # max in-memory jobs
EXPIRY_MINS = 60    # completed jobs expire after this many minutes


class Job:
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"


def _gc():
    """Remove old completed/failed jobs."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=EXPIRY_MINS)
    with _lock:
        to_del = [
            jid for jid, j in _jobs.items()
            if j["status"] in (Job.COMPLETED, Job.FAILED)
            and j.get("updated_at", datetime.now(timezone.utc).replace(tzinfo=None)) < cutoff
        ]
        for jid in to_del:
            del _jobs[jid]


def create_job(agent_id: str, filename: str, metadata: dict) -> str:
    """Create a new pending job. Returns job_id."""
    _gc()
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = {
            "job_id":    job_id,
            "agent_id":  agent_id,
            "filename":  filename,
            "metadata":  metadata,
            "status":    Job.PENDING,
            "progress":  0,
            "step":      "queued",
            "result":    None,
            "error":     None,
            "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
            "updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
        }
    logger.info(f"[Job] Created job {job_id} for {agent_id}/{filename}")
    return job_id


def get_job(job_id: str) -> Optional[Dict]:
    with _lock:
        job = _jobs.get(job_id)
        if job:
            return dict(job)
    return None


def get_all_jobs(agent_id: str = None, limit: int = 50) -> list:
    with _lock:
        jobs = list(_jobs.values())
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    if agent_id:
        jobs = [j for j in jobs if j.get("agent_id") == agent_id]
    return [_serialize_job(j) for j in jobs[:limit]]


def _update_job(job_id: str, **kwargs):
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)
            _jobs[job_id]["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None)


def _serialize_job(j: dict) -> dict:
    """Convert datetimes to strings for JSON serialisation."""
    out = {}
    for k, v in j.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif k == "metadata":
            # Don't expose full metadata in list view
            out[k] = {"agent_id": v.get("agent_id"), "filename": v.get("filename")}
        else:
            out[k] = v
    return out


# ─────────────────────────────────────────────────────────────
#  Job executor
# ─────────────────────────────────────────────────────────────
def submit_job(
    job_id:    str,
    file_path: str,
    metadata:  dict,
    pipeline_fn: Callable,
) -> None:
    """
    Run the pipeline in a background thread.
    pipeline_fn(file_path, metadata) → result dict
    """
    def _run():
        logger.info(f"[Job] Starting job {job_id}")
        _update_job(job_id, status=Job.RUNNING, progress=5, step="starting")

        try:
            _update_job(job_id, progress=15, step="transcribing")
            result = pipeline_fn(file_path, metadata)
            _update_job(
                job_id,
                status   = Job.COMPLETED if result.get("status") == "completed" else Job.FAILED,
                progress = 100,
                step     = "done",
                result   = result,
                error    = result.get("error"),
            )
            logger.info(f"[Job] Completed job {job_id}: {result.get('status')}")
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"[Job] Failed job {job_id}: {e}\n{tb}")
            _update_job(
                job_id,
                status   = Job.FAILED,
                progress = 0,
                step     = "failed",
                error    = str(e),
            )
        finally:
            # Clean up the temp file
            import os
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except OSError:
                pass

    t = threading.Thread(target=_run, daemon=True, name=f"job-{job_id[:8]}")
    t.start()
    return t


# ─────────────────────────────────────────────────────────────
#  Job status helpers for API
# ─────────────────────────────────────────────────────────────
def job_to_response(job: dict) -> dict:
    """Prepare a job dict for HTTP response serialisation."""
    out = _serialize_job(job)
    # Don't expose full result in status — client should call /api/calls/{call_id}
    if out.get("result") and isinstance(out["result"], dict):
        r = out["result"]
        out["call_id"]         = r.get("call_id")
        out["overall_rating"]  = (r.get("quality_analysis") or {}).get("overall_rating")
        out["result"]          = "see /api/calls/" + (r.get("call_id") or "")
    return out
