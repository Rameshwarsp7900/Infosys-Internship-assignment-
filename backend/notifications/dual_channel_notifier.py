"""
backend/notifications/dual_channel_notifier.py
────────────────────────────────────────────────
Two notification channels:

  CHANNEL 1 — Agent/QA Alerts
    Triggered by: low scores, unparliamentary language, sentiment escalation,
                  policy violations, FCR failures
    Audience:     QA Supervisors, Team Leads
    Config:       AGENT_SLACK_WEBHOOK_URL, AGENT_EMAIL_TO

  CHANNEL 2 — System Alerts
    Triggered by: API failures, processing errors, SLA breaches,
                  DB errors, LLM provider failures
    Audience:     Dev/IT team
    Config:       SYSTEM_SLACK_WEBHOOK_URL, SYSTEM_EMAIL_TO

Both channels support Slack webhooks + Resend email.
Both have independent throttle windows to prevent storm alerts.
"""

import os
import threading
import logging
from datetime import datetime, timedelta
from typing import Optional

try:
    from logger_setup import get_logger, log_alert_fired
    _log = get_logger(__name__)
except ImportError:
    _log = logging.getLogger(__name__)
    def log_alert_fired(*a, **k): pass


# ── Alert type classification ─────────────────────────────────────────────────
AGENT_ALERT_TYPES = {
    "unparliamentary_language",
    "low_quality_score",
    "sentiment_escalation",
    "policy_violation",
    "low_empathy",
    "low_compliance",
}

SYSTEM_ALERT_TYPES = {
    "processing_failure",
    "transcription_error",
    "llm_error",
    "sla_breach",
    "db_error",
    "api_quota_exceeded",
}


class DualChannelNotifier:
    """
    Routes alerts to the correct channel and sends via Slack + email.
    Throttles to prevent alert storms.
    """

    THROTTLE_MIN = int(os.getenv("ALERT_THROTTLE_MINUTES", "5"))

    def __init__(self):
        # Channel 1 — Agent/QA
        self.agent_slack    = os.getenv("AGENT_SLACK_WEBHOOK_URL", "")
        self.agent_email    = os.getenv("AGENT_EMAIL_TO", "")

        # Channel 2 — System
        self.system_slack   = os.getenv("SYSTEM_SLACK_WEBHOOK_URL", "")
        self.system_email   = os.getenv("SYSTEM_EMAIL_TO", "")

        # Shared email config
        self.resend_key     = os.getenv("RESEND_API_KEY", "")
        self.email_from     = os.getenv("ALERT_EMAIL_FROM", "criterion@yourdomain.com")

        # Throttle state
        self._throttle: dict = {}
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────────
    def notify_agent(
        self,
        alert_type: str,
        severity:   str,
        title:      str,
        message:    str,
        agent_id:   str,
        call_id:    str,
        details:    dict = None,
    ) -> dict:
        """Send to CHANNEL 1 (Agent/QA supervisors)."""
        return self._dispatch(
            channel    = "agent",
            alert_type = alert_type,
            severity   = severity,
            title      = title,
            message    = message,
            agent_id   = agent_id,
            call_id    = call_id,
            details    = details or {},
        )

    def notify_system(
        self,
        alert_type: str,
        severity:   str,
        title:      str,
        message:    str,
        component:  str = "pipeline",
        call_id:    str = None,
        details:    dict = None,
    ) -> dict:
        """Send to CHANNEL 2 (Dev/IT team)."""
        return self._dispatch(
            channel    = "system",
            alert_type = alert_type,
            severity   = severity,
            title      = title,
            message    = message,
            agent_id   = component,
            call_id    = call_id or "system",
            details    = details or {},
        )

    def notify_auto(
        self,
        alert_type: str,
        severity:   str,
        title:      str,
        message:    str,
        agent_id:   str = "system",
        call_id:    str = "system",
        details:    dict = None,
    ) -> dict:
        """Auto-route based on alert_type — used by AlertEngine."""
        if alert_type in SYSTEM_ALERT_TYPES:
            return self.notify_system(alert_type, severity, title, message, agent_id, call_id, details)
        return self.notify_agent(alert_type, severity, title, message, agent_id, call_id, details)

    # ── Internal dispatch ─────────────────────────────────────────────────────
    def _dispatch(self, channel: str, alert_type: str, severity: str,
                  title: str, message: str, agent_id: str, call_id: str, details: dict) -> dict:
        result = {"channel": channel, "in_app": True}
        log_alert_fired(call_id, agent_id, alert_type, severity, channel)

        # Only send external for high/critical
        if severity not in ("high", "critical"):
            return result

        # Throttle
        if self._is_throttled(channel, agent_id, alert_type, severity):
            result["throttled"] = True
            return result
        self._mark_sent(channel, agent_id, alert_type, severity)

        # Pick channel config
        slack_url = self.agent_slack if channel == "agent" else self.system_slack
        email_to  = self.agent_email if channel == "agent" else self.system_email
        label     = "QA Alert" if channel == "agent" else "System Alert"

        kwargs = dict(
            channel_label=label,
            alert_type=alert_type, severity=severity,
            title=title, message=message,
            agent_id=agent_id, call_id=call_id, details=details,
        )

        if slack_url:
            threading.Thread(target=self._slack, kwargs={**kwargs, "webhook_url": slack_url}, daemon=True).start()
            result["slack"] = "queued"

        if email_to and self.resend_key:
            threading.Thread(target=self._email, kwargs={**kwargs, "to": email_to}, daemon=True).start()
            result["email"] = "queued"

        return result

    # ── Slack ─────────────────────────────────────────────────────────────────
    def _slack(self, webhook_url: str, channel_label: str, alert_type: str, severity: str,
               title: str, message: str, agent_id: str, call_id: str, details: dict, **_):
        import requests as req
        colors  = {"critical": "#dc2626", "high": "#d97706", "medium": "#2563eb", "low": "#16a34a"}
        emojis  = {"critical": ":red_circle:", "high": ":large_orange_circle:", "medium": ":large_blue_circle:", "low": ":large_green_circle:"}
        ch_icon = ":bust_in_silhouette:" if "QA" in channel_label else ":computer:"

        payload = {
            "text": f"{emojis.get(severity,':bell:')} *{channel_label}* — {title}",
            "attachments": [{
                "color": colors.get(severity, "#6366f1"),
                "blocks": [
                    {"type":"header","text":{"type":"plain_text","text":f"{ch_icon} {title}","emoji":True}},
                    {"type":"section","fields":[
                        {"type":"mrkdwn","text":f"*Channel*\n{channel_label}"},
                        {"type":"mrkdwn","text":f"*Severity*\n`{severity.upper()}`"},
                        {"type":"mrkdwn","text":f"*Type*\n{alert_type.replace('_',' ').title()}"},
                        {"type":"mrkdwn","text":f"*Agent/Component*\n`{agent_id}`"},
                    ]},
                    {"type":"section","text":{"type":"mrkdwn","text":f"*Message*\n{message}"}},
                ],
            }]
        }
        try:
            req.post(webhook_url, json=payload, timeout=8)
        except Exception as e:
            _log.warning(f"[Slack/{channel_label}] Failed: {e}")

    # ── Email ─────────────────────────────────────────────────────────────────
    def _email(self, to: str, channel_label: str, alert_type: str, severity: str,
               title: str, message: str, agent_id: str, call_id: str, details: dict, **_):
        import requests as req
        colors = {"critical":"#dc2626","high":"#d97706","medium":"#2563eb","low":"#16a34a"}
        color  = colors.get(severity,"#6366f1")
        icon   = "👤" if "QA" in channel_label else "⚙️"

        html = f"""<!DOCTYPE html><html><body style="font-family:Inter,Arial,sans-serif;background:#f1f5f9;padding:20px">
<div style="max-width:560px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden">
  <div style="background:linear-gradient(135deg,#0d1b2a,#243447);padding:20px;text-align:center">
    <h1 style="color:#fff;margin:0;font-size:1.3rem">{icon} Criterion QA — {channel_label}</h1>
  </div>
  <div style="padding:20px">
    <span style="background:{color};color:#fff;padding:3px 12px;border-radius:20px;font-size:0.75rem;font-weight:700">{severity.upper()}</span>
    <span style="background:#e2e8f0;color:#334155;padding:3px 12px;border-radius:20px;font-size:0.75rem;margin-left:8px">{alert_type.replace('_',' ').title()}</span>
    <h2 style="font-size:1rem;margin:16px 0 8px">{title}</h2>
    <p style="color:#64748b;font-size:0.875rem">{message}</p>
    <table style="width:100%;border-collapse:collapse;background:#f8fafc;border-radius:8px;font-size:0.82rem;margin-top:14px">
      <tr><td style="padding:8px 12px;color:#64748b">Agent / Component</td><td style="padding:8px 12px"><code>{agent_id}</code></td></tr>
      <tr style="background:#f1f5f9"><td style="padding:8px 12px;color:#64748b">Call ID</td><td style="padding:8px 12px"><code>{str(call_id)[:16]}…</code></td></tr>
    </table>
  </div>
  <div style="background:#f8fafc;padding:12px;text-align:center;font-size:0.72rem;color:#94a3b8">Criterion QA v3 · {channel_label}</div>
</div></body></html>"""

        to_list = [e.strip() for e in to.split(",") if e.strip()]
        try:
            req.post("https://api.resend.com/emails",
                headers={"Authorization":f"Bearer {self.resend_key}","Content-Type":"application/json"},
                json={"from":self.email_from,"to":to_list,
                      "subject":f"[{severity.upper()}] {channel_label}: {title[:60]}","html":html},
                timeout=10)
        except Exception as e:
            _log.warning(f"[Email/{channel_label}] Failed: {e}")

    # ── Throttle ──────────────────────────────────────────────────────────────
    def _is_throttled(self, channel, agent_id, alert_type, severity) -> bool:
        key = (channel, agent_id, alert_type, severity)
        with self._lock:
            last = self._throttle.get(key)
            return last is not None and datetime.utcnow() - last < timedelta(minutes=self.THROTTLE_MIN)

    def _mark_sent(self, channel, agent_id, alert_type, severity):
        key = (channel, agent_id, alert_type, severity)
        with self._lock:
            self._throttle[key] = datetime.utcnow()
            cutoff = datetime.utcnow() - timedelta(hours=2)
            self._throttle = {k: v for k, v in self._throttle.items() if v > cutoff}


# ── Singleton ─────────────────────────────────────────────────────────────────
_notifier: Optional[DualChannelNotifier] = None

def get_dual_notifier() -> DualChannelNotifier:
    global _notifier
    if _notifier is None:
        _notifier = DualChannelNotifier()
    return _notifier
