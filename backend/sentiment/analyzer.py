"""
backend/sentiment/analyzer.py
──────────────────────────────
Fixed sentiment analyzer. Key bug fixes from v2:
  - Guard against None start/end timestamps
  - Guard against None or non-string text
  - TextBlob import error doesn't crash the module
  - Escalation detection handles < 3 customer points gracefully
  - avg_agent_score and avg_customer_score always return floats
  - _score_to_label uses strict float thresholds
"""

import re
from typing import List, Dict, Optional

try:
    from textblob import TextBlob
    HAS_TEXTBLOB = True
except ImportError:
    HAS_TEXTBLOB = False

POSITIVE_WORDS = {
    "thank","thanks","great","excellent","perfect","helpful","appreciate",
    "wonderful","fantastic","amazing","good","happy","pleased","satisfied",
    "resolved","solution","fixed","better","improved","smooth","quick","fast",
    "professional","kind","patient","understanding","clear","easy","awesome",
    "outstanding","brilliant","superb","love","best","pleasure","welcome",
}
NEGATIVE_WORDS = {
    "frustrated","angry","upset","terrible","horrible","awful","bad","worst",
    "useless","incompetent","failed","broken","wrong","issue","problem","error",
    "disappointed","dissatisfied","unacceptable","ridiculous","absurd","waste",
    "never","refuse","cancel","escalate","complaint","unhappy","poor","slow",
    "annoyed","furious","outraged","disgusting","unacceptable","pathetic",
}


class SentimentAnalyzer:
    """Segment-level sentiment scoring with timeline and escalation detection."""

    def analyze(self, segments: List[Dict]) -> Dict:
        """
        Returns:
            {
                overall, agent, customer: "positive|neutral|negative",
                avg_agent_score, avg_customer_score: float,
                escalation_detected: bool,
                escalation_point: float | None,
                timeline: [{time, score, label, speaker}]
            }
        """
        if not segments:
            return self._empty()

        timeline      = []
        agent_scores  = []
        cust_scores   = []

        for seg in segments:
            # --- BUG FIX: guard all field accesses ---
            text    = str(seg.get("text") or "").strip()
            speaker = str(seg.get("speaker") or "unknown").lower()
            start   = seg.get("start")
            start   = float(start) if start is not None else 0.0  # BUG FIX: None guard

            # Use Deepgram's pre-computed sentiment if available
            dg_sent = seg.get("sentiment")

            score = self._score_segment(text, dg_sent)
            label = self._score_to_label(score)

            timeline.append({
                "time":    round(start, 2),
                "score":   round(score, 3),
                "label":   label,
                "speaker": speaker,
            })

            if speaker == "agent":
                agent_scores.append(score)
            elif speaker == "customer":
                cust_scores.append(score)

        # --- BUG FIX: always compute floats even with empty lists ---
        avg_agent = float(sum(agent_scores) / len(agent_scores)) if agent_scores else 0.0
        avg_cust  = float(sum(cust_scores)  / len(cust_scores))  if cust_scores  else 0.0
        all_s     = [t["score"] for t in timeline]
        overall   = float(sum(all_s) / len(all_s)) if all_s else 0.0

        esc_detected, esc_point = self._detect_escalation(timeline)

        return {
            "overall":              self._score_to_label(overall),
            "agent":                self._score_to_label(avg_agent),
            "customer":             self._score_to_label(avg_cust),
            "avg_agent_score":      round(avg_agent, 3),
            "avg_customer_score":   round(avg_cust,  3),
            "escalation_detected":  esc_detected,
            "escalation_point":     esc_point,
            "timeline":             timeline,
        }

    # ── Scoring ──────────────────────────────────────────────────
    def _score_segment(self, text: str, dg_sentiment=None) -> float:
        """Return score in [-1, 1]."""
        # Use Deepgram's value if valid
        if dg_sentiment == "positive": return  0.65
        if dg_sentiment == "negative": return -0.65
        if dg_sentiment == "neutral":  return  0.0

        if not text:
            return 0.0

        # TextBlob path
        if HAS_TEXTBLOB:
            try:
                return float(TextBlob(text).sentiment.polarity)
            except Exception:
                pass

        # Keyword fallback
        return self._keyword_score(text)

    def _keyword_score(self, text: str) -> float:
        words = re.findall(r"\b\w+\b", text.lower())
        if not words:
            return 0.0
        pos = sum(1 for w in words if w in POSITIVE_WORDS)
        neg = sum(1 for w in words if w in NEGATIVE_WORDS)
        return (pos - neg) / max(len(words) * 0.5, 1.0)

    @staticmethod
    def _score_to_label(score: float) -> str:
        # BUG FIX: explicit float comparison, handle NaN/inf
        try:
            s = float(score)
        except (TypeError, ValueError):
            return "neutral"
        if s > 0.15:  return "positive"
        if s < -0.15: return "negative"
        return "neutral"

    # ── Escalation detection ─────────────────────────────────────
    def _detect_escalation(self, timeline: List[Dict]):
        """
        Escalation: customer sentiment drops > 0.3 in any 3-min window.
        BUG FIX: needs at least 2 customer points (was crashing on 0 or 1).
        """
        customer_pts = [
            (t["time"], t["score"])
            for t in timeline
            if t.get("speaker") == "customer"
        ]

        # BUG FIX: need at least 2 points to detect a drop
        if len(customer_pts) < 2:
            return False, None

        window = 180.0  # 3 minutes
        for i, (t1, s1) in enumerate(customer_pts):
            for t2, s2 in customer_pts[i+1:]:
                if t2 - t1 <= window:
                    if s1 - s2 > 0.3:
                        return True, round(t2, 2)
                else:
                    break

        return False, None

    @staticmethod
    def _empty() -> Dict:
        return {
            "overall": "neutral", "agent": "neutral", "customer": "neutral",
            "avg_agent_score": 0.0, "avg_customer_score": 0.0,
            "escalation_detected": False, "escalation_point": None,
            "timeline": [],
        }
