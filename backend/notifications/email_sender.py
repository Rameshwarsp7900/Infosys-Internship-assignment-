"""
notifications/email_sender.py
──────────────────────────────
Send alert emails via Resend (free tier: 3,000 emails/month).
Sign up: https://resend.com → API Keys

Required env vars:
    RESEND_API_KEY   = re_xxxxx
    ALERT_EMAIL_TO   = supervisor@yourcompany.com
    ALERT_EMAIL_FROM = criterion@yourdomain.com  (must be verified in Resend)

If RESEND_API_KEY is not set, falls back to a no-op with a warning log.
If ALERT_EMAIL_FROM is not set, uses onboarding@resend.dev (Resend sandbox, unverified).
"""

import os
import json
import logging
import requests
from typing import List, Optional

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"

# Severity → colour for the email badge
SEV_COLORS = {
    "critical": "#dc2626",
    "high":     "#d97706",
    "medium":   "#2563eb",
    "low":      "#16a34a",
}


def send_alert_email(
    alert_type: str,
    severity:   str,
    title:      str,
    message:    str,
    agent_id:   str,
    call_id:    str,
    details:    dict = None,
    to_emails:  Optional[List[str]] = None,
) -> bool:
    """
    Send an alert email via Resend.
    Returns True on success, False on failure.
    """
    api_key  = os.environ.get("RESEND_API_KEY")
    if not api_key:
        logger.warning("[Email] RESEND_API_KEY not set — email alert skipped")
        return False

    to_list = to_emails or [e.strip() for e in os.environ.get("ALERT_EMAIL_TO","").split(",") if e.strip()]
    if not to_list:
        logger.warning("[Email] ALERT_EMAIL_TO not set — email alert skipped")
        return False

    from_addr = os.environ.get("ALERT_EMAIL_FROM", "onboarding@resend.dev")
    sev_color = SEV_COLORS.get(severity, "#6366f1")
    det_html  = _render_details(details or {})

    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"/></head>
<body style="font-family:Inter,Arial,sans-serif;background:#f1f5f9;margin:0;padding:20px">
<div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.08)">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#6366f1,#8b5cf6);padding:24px;text-align:center">
    <h1 style="color:#fff;margin:0;font-size:1.4rem;font-weight:700">🔔 Criterion QA Alert</h1>
    <p style="color:rgba(255,255,255,0.8);margin:6px 0 0;font-size:0.875rem">AI-Powered Call Quality Auditor</p>
  </div>

  <!-- Severity badge -->
  <div style="padding:20px 28px 0">
    <span style="background:{sev_color};color:#fff;padding:4px 14px;border-radius:20px;font-size:0.75rem;font-weight:700;text-transform:uppercase;letter-spacing:0.05em">{severity}</span>
    <span style="background:#e0e7ff;color:#4338ca;padding:4px 14px;border-radius:20px;font-size:0.75rem;font-weight:600;margin-left:8px">{alert_type.replace("_"," ").title()}</span>
  </div>

  <!-- Content -->
  <div style="padding:20px 28px">
    <h2 style="font-size:1.1rem;color:#0f172a;margin:0 0 10px">{title}</h2>
    <p style="color:#64748b;font-size:0.9rem;line-height:1.6;margin:0 0 16px">{message}</p>

    <!-- Meta -->
    <table style="width:100%;border-collapse:collapse;background:#f8fafc;border-radius:8px;overflow:hidden">
      <tr>
        <td style="padding:10px 14px;font-size:0.8rem;color:#64748b;border-bottom:1px solid #e2e8f0"><strong>Agent ID</strong></td>
        <td style="padding:10px 14px;font-size:0.8rem;color:#0f172a;border-bottom:1px solid #e2e8f0;font-family:monospace">{agent_id}</td>
      </tr>
      <tr>
        <td style="padding:10px 14px;font-size:0.8rem;color:#64748b"><strong>Call ID</strong></td>
        <td style="padding:10px 14px;font-size:0.8rem;color:#0f172a;font-family:monospace">{call_id[:16]}…</td>
      </tr>
    </table>

    {det_html}
  </div>

  <!-- Footer -->
  <div style="background:#f8fafc;padding:16px 28px;text-align:center;border-top:1px solid #e2e8f0">
    <p style="color:#94a3b8;font-size:0.75rem;margin:0">Criterion QA v2 · Automated Alert System</p>
  </div>
</div>
</body>
</html>
"""

    payload = {
        "from":    from_addr,
        "to":      to_list,
        "subject": f"[{severity.upper()}] Criterion QA: {title[:60]}",
        "html":    html_body,
    }

    try:
        resp = requests.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type":  "application/json",
            },
            json=payload,
            timeout=10,
        )
        if resp.status_code in (200, 201):
            logger.info(f"[Email] Alert sent to {to_list}: {title}")
            return True
        else:
            logger.error(f"[Email] Resend error {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"[Email] Failed to send alert email: {e}")
        return False


def _render_details(details: dict) -> str:
    if not details:
        return ""
    rows = ""
    for k, v in details.items():
        if v is not None:
            label = k.replace("_", " ").title()
            rows += f"""<tr>
              <td style="padding:8px 14px;font-size:0.78rem;color:#64748b;border-bottom:1px solid #e2e8f0">{label}</td>
              <td style="padding:8px 14px;font-size:0.78rem;color:#0f172a;border-bottom:1px solid #e2e8f0">{v}</td>
            </tr>"""
    if not rows:
        return ""
    return f"""
    <div style="margin-top:16px">
      <p style="font-size:0.82rem;font-weight:600;color:#64748b;margin:0 0 8px;text-transform:uppercase;letter-spacing:0.04em">Details</p>
      <table style="width:100%;border-collapse:collapse;background:#f8fafc;border-radius:8px;overflow:hidden">{rows}</table>
    </div>"""
