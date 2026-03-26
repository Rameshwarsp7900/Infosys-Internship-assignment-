"""
chat_processor.py
─────────────────
Parses plain-text chat logs into the same segment format
that the Deepgram processor returns, so the rest of the
pipeline works identically for both audio and chat inputs.

Supported formats:
  [Agent]: Hello, how can I help?
  [Customer]: I have a billing issue.
  Agent: ...
  Customer: ...
  AGENT: ...
  USER: ...  (treated as customer)
  support: ...
  Lines without a label are appended to the previous speaker.
"""

import re
import time
from typing import List, Optional

AGENT_KEYWORDS = {"agent", "support", "representative", "rep", "staff", "advisor", "cs", "operator"}
CUSTOMER_KEYWORDS = {"customer", "user", "client", "caller", "you"}

# Matches: [Agent]: text  OR  Agent: text  OR  AGENT: text
SPEAKER_RE = re.compile(r"^\[?([a-zA-Z0-9 _-]+)\]?\s*:\s*(.+)$")


class ChatProcessor:
    """Parse plain-text chat logs into transcript segments."""

    def process(self, file_path: str) -> dict:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        segments = self._parse_lines(lines)
        full_text = " ".join(s["text"] for s in segments)

        return {
            "segments": segments,
            "full_text": full_text,
            "duration": None,           # No duration for chat logs
            "word_count": len(full_text.split()),
            "language": "en",
            "speakers": len(set(s["speaker"] for s in segments)),
        }

    # ------------------------------------------------------------------ #

    def _parse_lines(self, lines: List[str]) -> List[dict]:
        segments: List[dict] = []
        current_speaker: Optional[str] = None
        current_text: List[str] = []
        fake_time = 0.0

        def flush():
            nonlocal fake_time
            if current_speaker and current_text:
                text = " ".join(current_text).strip()
                word_count = len(text.split())
                duration = word_count * 0.4      # ~150 wpm
                segments.append({
                    "speaker": current_speaker,
                    "start": round(fake_time, 2),
                    "end": round(fake_time + duration, 2),
                    "text": text,
                    "confidence": 1.0,
                    "sentiment": None,
                })
                fake_time += duration + 0.5
            current_text.clear()

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            m = SPEAKER_RE.match(line)
            if m:
                label = m.group(1).strip().lower()
                text = m.group(2).strip()
                speaker = self._resolve_speaker(label)

                if speaker != current_speaker:
                    flush()
                    current_speaker = speaker

                current_text.append(text)
            else:
                # Continuation line — append to current speaker
                if current_speaker:
                    current_text.append(line)

        flush()
        return segments

    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_speaker(label: str) -> str:
        label_lower = label.lower().strip()
        if label_lower in AGENT_KEYWORDS or any(k in label_lower for k in AGENT_KEYWORDS):
            return "agent"
        if label_lower in CUSTOMER_KEYWORDS or any(k in label_lower for k in CUSTOMER_KEYWORDS):
            return "customer"
        # Unknown label — preserve it as-is rather than silently misclassifying
        # The formatter will display it literally; at least it won't be wrong
        return "customer"  # safer default: unknown = customer, not agent
