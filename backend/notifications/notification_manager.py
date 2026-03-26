"""
notifications/notification_manager.py
──────────────────────────────────────
Single entry point for all outbound alert notifications.

Channels:
  1. In-app    → Written to DB (always, via alert_engine.py)
  2. Email     → Resend API   (if RESEND_API_KEY + ALERT_EMAIL_TO set)
  3. Slack     → Incoming Webhook (if SLACK_WEBHOOK_URL set)
  4. Webhook   → Generic HTTP    (if WEBHOOK_URL set)

Throttling: same (agent_id, alert_type, severity) fires external
notifications at most once per THROTTLE_MINUTES window, preventing
alert storms on batch processing.
"""

import os
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List

logger = logging.getLogger(__name__)

THROTTLE_MINUTES = int(os.environ.get("ALERT_THROTTLE_MINUTES", "5"))

# In-memory throttle table: {(agent_id, alert_type, severity): last_sent_datetime}
_throttle: Dict[tuple, datetime] = {}
_throttle_lock = threading.Lock()


class NotificationManager:
    """
    Dispatches alerts to all configured channels.
    Call notify() once per alert; it fans out appropriately.
    """

    # Severity levels that trigger external notifications
    EXTERNAL_SEVERITIES = {"critical", "high"}

    def notify(
        self,
        alert_type: str,
        severity:   str,
        title:      str,
        message:    str,
        agent_id:   str,
        call_id:    str,
        details:    dict = None,
    ) -> Dict[str, bool]:
        """
        Fire all configured channels. Returns {channel: success}.
        In-app channel is always True (handled by alert_engine/db).
        """
        results = {"in_app": True}  # Already written by alert_engine

        # Only send external notifications for high/critical
        if severity not in self.EXTERNAL_SEVERITIES:
            return results

        # Throttle check
        if self._is_throttled(agent_id, alert_type, severity):
            logger.debug(f"[Notify] Throttled: {agent_id}/{alert_type}/{severity}")
            results["throttled"] = True
            return results

        self._mark_sent(agent_id, alert_type, severity)

        # Fan out to external channels in background threads
        kwargs = dict(
            alert_type=alert_type, severity=severity,
            title=title, message=message,
            agent_id=agent_id, call_id=call_id, details=details,
        )

        # Email
        if os.environ.get("RESEND_API_KEY"):
            t = threading.Thread(target=self._send_email, kwargs=kwargs, daemon=True)
            t.start()
            results["email"] = "queued"

        # Slack
        if os.environ.get("SLACK_WEBHOOK_URL"):
            t = threading.Thread(target=self._send_slack, kwargs=kwargs, daemon=True)
            t.start()
            results["slack"] = "queued"

        # Generic Webhook
        if os.environ.get("WEBHOOK_URL"):
            t = threading.Thread(target=self._send_webhook, kwargs=kwargs, daemon=True)
            t.start()
            results["webhook"] = "queued"

        return results

    # ── Channel senders ─────────────────────────────────────
    def _send_email(self, **kwargs):
        try:
            import importlib, sys
            m = importlib.import_module("notifications.email_sender")
            m.send_alert_email(**kwargs)
        except Exception as e:
            logger.error(f"[Notify/Email] {e}")

    def _send_slack(self, **kwargs):
        try:
            import importlib
            m = importlib.import_module("notifications.webhook_sender")
            m.send_slack_alert(**kwargs)
        except Exception as e:
            logger.error(f"[Notify/Slack] {e}")

    def _send_webhook(self, **kwargs):
        try:
            import importlib
            m = importlib.import_module("notifications.webhook_sender")
            m.send_webhook_alert(**kwargs)
        except Exception as e:
            logger.error(f"[Notify/Webhook] {e}")

    # ── Throttle ─────────────────────────────────────────────
    def _is_throttled(self, agent_id, alert_type, severity) -> bool:
        key = (agent_id, alert_type, severity)
        with _throttle_lock:
            last = _throttle.get(key)
            if last is None:
                return False
            return datetime.now(timezone.utc).replace(tzinfo=None) - last < timedelta(minutes=THROTTLE_MINUTES)

    def _mark_sent(self, agent_id, alert_type, severity):
        key = (agent_id, alert_type, severity)
        with _throttle_lock:
            _throttle[key] = datetime.now(timezone.utc).replace(tzinfo=None)
            # Prune stale entries
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
            to_del = [k for k, v in _throttle.items() if v < cutoff]
            for k in to_del:
                del _throttle[k]


# Global singleton
_manager: NotificationManager = None

def get_notifier() -> NotificationManager:
    global _manager
    if _manager is None:
        _manager = NotificationManager()
    return _manager
