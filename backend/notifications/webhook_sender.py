"""
notifications/webhook_sender.py
─────────────────────────────────
Send alerts to Slack (via Incoming Webhooks) and/or a generic HTTP webhook.

Slack setup (free):
  1. Go to https://api.slack.com/apps → Create New App → From Scratch
  2. Add feature: Incoming Webhooks → Activate
  3. Add to Workspace → pick channel → copy Webhook URL
  4. Set env var: SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../xxx

Generic webhook setup:
  Set env var: WEBHOOK_URL=https://your-endpoint.com/criterion-alerts
  Payload is JSON with all alert fields.

Both are optional. If neither env var is set, this is a no-op.
"""

import os
import json
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

# Severity → Slack colour bar
SEV_COLORS = {
    "critical": "#dc2626",
    "high":     "#d97706",
    "medium":   "#2563eb",
    "low":      "#16a34a",
}

# Severity → Slack emoji
SEV_EMOJI = {
    "critical": ":red_circle:",
    "high":     ":large_orange_circle:",
    "medium":   ":large_blue_circle:",
    "low":      ":large_green_circle:",
}

# Alert type → emoji
TYPE_EMOJI = {
    "unparliamentary_language": ":no_mouth:",
    "low_quality_score":        ":chart_with_downwards_trend:",
    "sentiment_escalation":     ":anger:",
    "policy_violation":         ":clipboard:",
    "sla_breach":               ":clock1:",
    "processing_failure":       ":x:",
}


def send_slack_alert(
    alert_type: str,
    severity:   str,
    title:      str,
    message:    str,
    agent_id:   str,
    call_id:    str,
    details:    dict = None,
) -> bool:
    """
    Send a formatted alert to Slack via Incoming Webhook.
    Returns True on success.
    """
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        logger.debug("[Slack] SLACK_WEBHOOK_URL not set — Slack alert skipped")
        return False

    sev_color  = SEV_COLORS.get(severity, "#6366f1")
    sev_emoji  = SEV_EMOJI.get(severity,   ":bell:")
    type_emoji = TYPE_EMOJI.get(alert_type, ":warning:")
    det_text   = _format_details(details or {})

    payload = {
        "text": f"{sev_emoji} *Criterion QA Alert* — {title}",
        "attachments": [
            {
                "color": sev_color,
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": f"{type_emoji} {title}", "emoji": True},
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*Severity*\n`{severity.upper()}`"},
                            {"type": "mrkdwn", "text": f"*Type*\n{alert_type.replace('_',' ').title()}"},
                            {"type": "mrkdwn", "text": f"*Agent*\n`{agent_id}`"},
                            {"type": "mrkdwn", "text": f"*Call ID*\n`{call_id[:12]}…`"},
                        ],
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*Message*\n{message}"},
                    },
                ],
            }
        ],
    }

    if det_text:
        payload["attachments"][0]["blocks"].append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Details*\n{det_text}"},
        })

    # Footer divider
    payload["attachments"][0]["blocks"].append({"type": "divider"})
    payload["attachments"][0]["footer"] = "Criterion QA v2 · Automated Alerts"

    try:
        resp = requests.post(webhook_url, json=payload, timeout=8)
        if resp.status_code == 200 and resp.text == "ok":
            logger.info(f"[Slack] Alert sent: {title}")
            return True
        else:
            logger.error(f"[Slack] Error {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"[Slack] Request failed: {e}")
        return False


def send_webhook_alert(
    alert_type: str,
    severity:   str,
    title:      str,
    message:    str,
    agent_id:   str,
    call_id:    str,
    details:    dict = None,
) -> bool:
    """
    POST alert payload to a generic webhook URL.
    Payload is JSON — integrate with Zapier, Make, PagerDuty, etc.
    """
    webhook_url = os.environ.get("WEBHOOK_URL")
    if not webhook_url:
        logger.debug("[Webhook] WEBHOOK_URL not set — webhook skipped")
        return False

    payload = {
        "source":     "criterion_qa",
        "version":    "2.0",
        "alert_type": alert_type,
        "severity":   severity,
        "title":      title,
        "message":    message,
        "agent_id":   agent_id,
        "call_id":    call_id,
        "details":    details or {},
    }

    try:
        resp = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json", "X-Source": "criterion-qa"},
            timeout=10,
        )
        if resp.ok:
            logger.info(f"[Webhook] Alert sent to {webhook_url}: {title}")
            return True
        else:
            logger.error(f"[Webhook] Error {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"[Webhook] Request failed: {e}")
        return False


def _format_details(details: dict) -> str:
    if not details:
        return ""
    lines = []
    for k, v in details.items():
        if v is not None:
            lines.append(f"• *{k.replace('_',' ').title()}*: `{v}`")
    return "\n".join(lines)
