"""
alerts/alert_engine.py
───────────────────────
Rule-based alert detection engine.

Checks:
  1. Unparliamentary / offensive words (per segment)
  2. Low quality scores (overall, compliance, empathy)
  3. Sentiment escalation (customer going negative)
  4. Policy violations (from RAG engine)
  5. Processing failures (system-level)
  6. Performance SLA breach (processing time)

All alerts are written to the database via db.insert_alert().
"""

import re
from typing import List, Dict, Optional, Tuple

# ─────────────────────────────────────────────────────────────
#  Unparliamentary word list
# ─────────────────────────────────────────────────────────────
UNPARLIAMENTARY_WORDS: List[str] = [
    # Derogatory / offensive
    "idiot", "stupid", "moron", "dumb", "fool", "imbecile", "dimwit",
    "useless", "worthless", "incompetent", "pathetic", "loser",
    # Rude/dismissive
    "shut up", "shut your mouth", "get lost", "go away", "none of your business",
    "i don't care", "not my problem", "deal with it", "take it or leave it",
    "whatever", "yeah right", "obviously",
    # Threatening / aggressive
    "sue you", "take you to court", "legal action", "lawyer up",
    "report you", "fire you", "get you fired", "your job",
    # Aggressive expressions
    "this is ridiculous", "this is absurd", "complete waste",
    "terrible service", "awful service", "worst ever",
    # Mild profanity (context-appropriate escalation)
    "damn", "crap", "hell", "bloody",
]

# Compile patterns for efficiency
_PATTERNS = [
    (re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE), w)
    for w in UNPARLIAMENTARY_WORDS
]

# ─────────────────────────────────────────────────────────────
#  Thresholds
# ─────────────────────────────────────────────────────────────
SCORE_THRESHOLDS = {
    "overall_rating":      {"critical": 4.0, "high": 5.0, "medium": 6.5},
    "empathy":             {"critical": 3.0, "high": 4.5, "medium": 5.5},
    "compliance":          {"critical": 3.0, "high": 5.0, "medium": 6.0},
    "professionalism":     {"critical": 3.5, "high": 5.0, "medium": 6.0},
    "customer_satisfaction":{"critical":3.0, "high": 4.5, "medium": 5.5},
}

SLA_MAX_SECONDS = 60   # Performance SLA for short files


class AlertEngine:
    """Detects all alert conditions, records them in DB, and fires notifications."""

    def __init__(self, db=None, notifier=None):
        self._db       = db
        self._notifier = notifier  # NotificationManager for email/Slack/webhook

    # ─────────────────────────────────────────────────────────
    #  Main entry point
    # ─────────────────────────────────────────────────────────
    def evaluate(
        self,
        call_id:   str,
        agent_id:  str,
        quality:   dict,
        sentiment: dict,
        segments:  List[Dict],
        policy_violations: List[Dict] = None,
        processing_time: float = None,
    ) -> List[Dict]:
        """
        Run all checks. Returns list of alert dicts generated.
        Also writes to DB if db module is available.
        """
        alerts = []

        # 1. Unparliamentary words
        hits, word_alerts = self._check_unparliamentary(call_id, agent_id, segments)
        alerts.extend(word_alerts)

        # 2. Quality score thresholds
        alerts.extend(self._check_scores(call_id, agent_id, quality))

        # 3. Sentiment escalation
        if sentiment.get("escalation_detected"):
            alerts.extend(self._check_escalation(call_id, agent_id, sentiment))

        # 4. Policy violations
        if policy_violations:
            alerts.extend(self._check_policy(call_id, agent_id, policy_violations))

        # 5. Processing SLA
        if processing_time and processing_time > SLA_MAX_SECONDS:
            alerts.extend(self._check_sla(call_id, agent_id, processing_time))

        # Write all to DB + fire external notifications
        for a in alerts:
            if self._db:
                self._db.insert_alert(
                    call_id=a["call_id"],
                    agent_id=a["agent_id"],
                    alert_type=a["alert_type"],
                    severity=a["severity"],
                    title=a["title"],
                    message=a["message"],
                    details=a.get("details", {}),
                )
            if self._notifier:
                self._notifier.notify(
                    alert_type=a["alert_type"],
                    severity=a["severity"],
                    title=a["title"],
                    message=a["message"],
                    agent_id=a["agent_id"],
                    call_id=a["call_id"],
                    details=a.get("details", {}),
                )
        if self._db and hits:
            self._db.insert_unparliamentary_hits(call_id, hits)

        return alerts

    # ─────────────────────────────────────────────────────────
    #  Check 1: Unparliamentary words
    # ─────────────────────────────────────────────────────────
    def _check_unparliamentary(
        self, call_id: str, agent_id: str, segments: List[Dict]
    ) -> Tuple[List[Dict], List[Dict]]:
        hits    = []
        alerts  = []
        seen    = set()

        for seg in segments:
            text    = seg.get("text", "")
            speaker = seg.get("speaker", "unknown")
            start   = seg.get("start", 0)

            for pattern, word in _PATTERNS:
                if pattern.search(text) and word not in seen:
                    seen.add(word)
                    context = text[:100]
                    hits.append({
                        "word": word, "timestamp": start,
                        "speaker": speaker, "context": context,
                    })
                    severity = "critical" if speaker == "agent" else "high"
                    alerts.append({
                        "call_id": call_id, "agent_id": agent_id,
                        "alert_type": "unparliamentary_language",
                        "severity": severity,
                        "title": f"Unparliamentary language detected: '{word}'",
                        "message": f"Speaker ({speaker}) used inappropriate language: '{word}' at {self._fmt_time(start)}",
                        "details": {"word": word, "speaker": speaker, "timestamp": start, "context": context},
                    })

        return hits, alerts

    # ─────────────────────────────────────────────────────────
    #  Check 2: Quality scores
    # ─────────────────────────────────────────────────────────
    def _check_scores(self, call_id: str, agent_id: str, quality: dict) -> List[Dict]:
        alerts = []
        metrics = quality.get("metrics", {})

        def check_metric(name, score):
            thresholds = SCORE_THRESHOLDS.get(name, {})
            for sev in ["critical", "high", "medium"]:
                threshold = thresholds.get(sev)
                if threshold and score < threshold:
                    label = name.replace("_", " ").title()
                    alerts.append({
                        "call_id": call_id, "agent_id": agent_id,
                        "alert_type": "low_quality_score",
                        "severity": sev,
                        "title": f"Low {label} score: {score:.1f}/10",
                        "message": f"{label} score ({score:.1f}) is below {sev} threshold ({threshold})",
                        "details": {"metric": name, "score": score, "threshold": threshold},
                    })
                    break  # Only fire highest severity

        check_metric("overall_rating", quality.get("overall_rating", 10))
        for metric_name, metric_data in metrics.items():
            check_metric(metric_name, metric_data.get("score", 10))

        return alerts

    # ─────────────────────────────────────────────────────────
    #  Check 3: Sentiment escalation
    # ─────────────────────────────────────────────────────────
    def _check_escalation(self, call_id: str, agent_id: str, sentiment: dict) -> List[Dict]:
        ep = sentiment.get("escalation_point")
        return [{
            "call_id": call_id, "agent_id": agent_id,
            "alert_type": "sentiment_escalation",
            "severity": "high",
            "title": "Customer sentiment escalation detected",
            "message": f"Customer sentiment dropped sharply{f' around {self._fmt_time(ep)}' if ep else ''}. Possible escalation risk.",
            "details": {"escalation_point": ep, "customer_sentiment": sentiment.get("customer")},
        }]

    # ─────────────────────────────────────────────────────────
    #  Check 4: Policy violations
    # ─────────────────────────────────────────────────────────
    def _check_policy(self, call_id: str, agent_id: str, violations: List[Dict]) -> List[Dict]:
        alerts = []
        for v in violations:
            sev = v.get("severity", "medium")
            alerts.append({
                "call_id": call_id, "agent_id": agent_id,
                "alert_type": "policy_violation",
                "severity": sev,
                "title": f"Policy violation: {v.get('rule_text', 'Unknown rule')[:60]}",
                "message": v.get("violation", "Policy not followed"),
                "details": v,
            })
        return alerts

    # ─────────────────────────────────────────────────────────
    #  Check 5: SLA breach
    # ─────────────────────────────────────────────────────────
    def _check_sla(self, call_id: str, agent_id: str, processing_time: float) -> List[Dict]:
        return [{
            "call_id": call_id, "agent_id": agent_id,
            "alert_type": "sla_breach",
            "severity": "medium",
            "title": f"Processing SLA breached ({processing_time:.0f}s > {SLA_MAX_SECONDS}s)",
            "message": f"Call took {processing_time:.1f}s to process, exceeding the {SLA_MAX_SECONDS}s SLA.",
            "details": {"processing_time": processing_time, "sla_seconds": SLA_MAX_SECONDS},
        }]

    # ─────────────────────────────────────────────────────────
    #  Helper
    # ─────────────────────────────────────────────────────────
    @staticmethod
    def _fmt_time(seconds) -> str:
        if seconds is None:
            return "unknown"
        m = int(seconds) // 60
        s = int(seconds) % 60
        return f"{m}:{s:02d}"
