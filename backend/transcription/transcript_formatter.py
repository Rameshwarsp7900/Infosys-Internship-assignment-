"""
transcript_formatter.py
───────────────────────
Converts the raw segment list into a clean formatted string
that is sent to the LLM for quality scoring.
"""


class TranscriptFormatter:
    def format_for_llm(self, segments: list) -> str:
        """Return a clean AGENT/CUSTOMER labelled string."""
        lines = []
        for seg in segments:
            speaker = seg.get("speaker", "unknown").upper()
            text = seg.get("text", "").strip()
            if text:
                lines.append(f"{speaker}: {text}")
        return "\n".join(lines)

    def format_as_html(self, segments: list) -> str:
        """Return colour-coded HTML for the transcript viewer."""
        html = []
        for seg in segments:
            speaker = seg.get("speaker", "unknown")
            text = seg.get("text", "").strip()
            start = seg.get("start")
            css_class = "agent-line" if speaker == "agent" else "customer-line"
            timestamp = f'<span class="ts">[{self._fmt_time(start)}]</span> ' if start is not None else ""
            html.append(
                f'<p class="seg {css_class}">'
                f'<strong>{speaker.capitalize()}</strong>: {timestamp}{text}</p>'
            )
        return "\n".join(html)

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        if seconds is None:
            return "—"
        m = int(seconds) // 60
        s = int(seconds) % 60
        return f"{m}:{s:02d}"
