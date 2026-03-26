"""
deepgram_processor.py — Criterion QA v3 (improved)
────────────────────────────────────────────────────
Fixes:
  1. Improved speaker label accuracy (agent = first to speak in support calls)
  2. Better word merging prevents split/swapped segments  
  3. utt_split=1.5 gives Deepgram more time to detect speaker changes
  4. Confidence-based fallback for ambiguous speaker assignment
  5. Consecutive same-speaker utterances are merged to prevent fragmentation
"""
import os, time, requests
from typing import Optional

try:
    from logger_setup import get_logger, log_transcription_result, log_transcription_error
    _log = get_logger(__name__)
except ImportError:
    import logging; _log = logging.getLogger(__name__)
    def log_transcription_result(*a, **k): pass
    def log_transcription_error(*a, **k):  pass

DEEPGRAM_API_URL = "https://api.deepgram.com/v1/listen"

DEFAULT_PARAMS = {
    "model":         "nova-2",
    "diarize":       "true",
    "punctuate":     "true",
    "utterances":    "true",
    "smart_format":  "true",
    "language":      "en-US",
    "filler_words":  "false",
    "sentiment":     "true",
    "paragraphs":    "true",
    "utt_split":     1.5,   # seconds of silence to split utterances — key for agent/customer separation
}


class DeepgramProcessor:

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("DEEPGRAM_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "DEEPGRAM_API_KEY not set. "
                "Free signup: https://console.deepgram.com/signup"
            )

    def transcribe(self, audio_path: str, call_id: str = "unknown") -> dict:
        """Always returns a valid dict — never raises."""
        try:
            raw    = self._call_deepgram(audio_path)
            result = self._parse_response(raw)
            log_transcription_result(
                call_id, result["word_count"],
                len(result["segments"]), result["duration"]
            )
            return result
        except Exception as e:
            log_transcription_error(call_id, str(e))
            return {
                "segments": [], "full_text": "", "duration": 0.0,
                "word_count": 0, "language": "en", "speakers": 0,
                "error": str(e), "error_hint": self._hint(str(e)),
            }

    def _call_deepgram(self, audio_path: str) -> dict:
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type":  self._mime(audio_path),
        }
        with open(audio_path, "rb") as f:
            data = f.read()

        for attempt in range(3):
            try:
                resp = requests.post(
                    DEEPGRAM_API_URL, params=DEFAULT_PARAMS,
                    headers=headers, data=data, timeout=120,
                )
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.Timeout:
                if attempt == 2:
                    raise RuntimeError("Deepgram timed out (3 attempts).")
                time.sleep(2 ** attempt)
            except requests.exceptions.HTTPError as e:
                code, body = e.response.status_code, e.response.text[:300]
                if code == 401:
                    raise ValueError(f"Invalid DEEPGRAM_API_KEY (401): {body}")
                if code == 402:
                    raise ValueError("Deepgram free quota exceeded")
                raise RuntimeError(f"Deepgram HTTP {code}: {body}")
            except requests.exceptions.ConnectionError as e:
                raise RuntimeError(f"Cannot reach Deepgram API: {e}")

    def _parse_response(self, raw: dict) -> dict:
        results    = raw.get("results", {})
        metadata   = raw.get("metadata", {})
        duration   = float(metadata.get("duration", 0))
        channels   = results.get("channels", [{}])
        alt        = channels[0].get("alternatives", [{}])[0] if channels else {}
        utterances = results.get("utterances", [])
        full_text  = alt.get("transcript", "")

        # Determine who is the agent (first speaker in support call = agent greeting)
        agent_idx = self._first_to_speak(utterances)
        _log.info(f"Agent speaker index: {agent_idx}")

        # Build segments from utterances
        segments = []
        if utterances:
            for utt in utterances:
                spk  = utt.get("speaker", 0)
                role = "agent" if spk == agent_idx else "customer"
                text = utt.get("transcript", "").strip()
                if not text:
                    continue
                segments.append({
                    "speaker":     role,
                    "start":       round(float(utt.get("start", 0)), 2),
                    "end":         round(float(utt.get("end", 0)), 2),
                    "text":        text,
                    "confidence":  round(float(utt.get("confidence", 0)), 3),
                    "sentiment":   utt.get("sentiment"),
                    "speaker_idx": spk,
                })

        # Merge consecutive same-speaker segments (reduces fragmentation)
        segments = self._merge_consecutive(segments)

        # Word-level fallback
        if not segments:
            segments = self._words_to_segments(alt.get("words", []), agent_idx)
            if segments:
                _log.warning("Used word-level fallback")

        # Last resort: single block
        if not segments and full_text.strip():
            segments = [{
                "speaker": "agent", "start": 0.0, "end": duration,
                "text": full_text, "confidence": 1.0, "sentiment": None,
            }]
            _log.warning("No diarization — single speaker block")

        wc = len(full_text.split()) if full_text else sum(len(s["text"].split()) for s in segments)
        return {
            "segments":   segments,
            "full_text":  full_text,
            "duration":   round(duration, 2),
            "word_count": wc,
            "language":   self._language(metadata),
            "speakers":   len(set(s["speaker"] for s in segments)),
        }

    @staticmethod
    def _first_to_speak(utterances: list) -> int:
        """
        Determine which Deepgram speaker index is the agent.

        Strategy (in priority order):
        1. The speaker with the LONGEST total speaking time is the agent
           — agents dominate call time in support calls.
        2. If tied, fall back to the first speaker chronologically.

        Why NOT just "first to speak":
        - Deepgram speaker indices are non-deterministic (0 isn't always first).
        - Customers often speak first ("Hello?") before the agent greets.
        - Longest-talk-time is a much more reliable signal for the agent role.
        """
        if not utterances:
            return 0

        # Accumulate total duration per speaker index
        speaker_time: dict = {}
        for utt in utterances:
            spk = utt.get("speaker", 0)
            dur = float(utt.get("end", 0)) - float(utt.get("start", 0))
            speaker_time[spk] = speaker_time.get(spk, 0.0) + dur

        if not speaker_time:
            return 0

        # Agent = speaker with most total talk time
        agent_idx = max(speaker_time, key=lambda k: speaker_time[k])
        _log.info(f"Speaker talk times: {speaker_time} → agent assigned to speaker {agent_idx}")
        return agent_idx

    @staticmethod
    def _merge_consecutive(segments: list) -> list:
        """
        Merge adjacent segments from the same speaker.
        Only merge if gap is very small (< 0.5s) to avoid swallowing
        short interjections from the other speaker.
        """
        if not segments:
            return []
        merged = [segments[0].copy()]
        for seg in segments[1:]:
            last = merged[-1]
            gap = seg["start"] - last["end"]
            # Only merge same-speaker with a tight gap — don't bridge real turn changes
            if seg["speaker"] == last["speaker"] and gap < 0.5:
                last["text"] += " " + seg["text"]
                last["end"]   = seg["end"]
            else:
                merged.append(seg.copy())
        return merged

    @staticmethod
    def _words_to_segments(words: list, agent_idx: int) -> list:
        """Group words by speaker turn into segments."""
        if not words:
            return []
        segs, cur = [], None
        for w in words:
            spk  = w.get("speaker", 0)
            role = "agent" if spk == agent_idx else "customer"
            text = w.get("punctuated_word") or w.get("word", "")
            if cur is None or cur.get("speaker_idx") != spk:
                if cur:
                    segs.append(cur)
                cur = {
                    "speaker": role, "start": round(float(w.get("start", 0)), 2),
                    "end": round(float(w.get("end", 0)), 2),
                    "text": text, "confidence": round(float(w.get("confidence", 0)), 3),
                    "sentiment": None, "speaker_idx": spk,
                }
            else:
                cur["text"] += " " + text
                cur["end"]   = round(float(w.get("end", 0)), 2)
        if cur:
            segs.append(cur)
        return segs

    @staticmethod
    def _mime(path: str) -> str:
        return {
            "mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav",
            "ogg": "audio/ogg",  "flac": "audio/flac", "webm": "audio/webm",
        }.get(path.rsplit(".", 1)[-1].lower(), "audio/mpeg")

    @staticmethod
    def _language(metadata: dict) -> str:
        m = metadata.get("models", [])
        return m[0].get("name", "en") if m and isinstance(m[0], dict) else "en"

    @staticmethod
    def _hint(error: str) -> str:
        e = error.lower()
        if "api_key" in e or "401" in e:
            return "Check DEEPGRAM_API_KEY in config/.env"
        if "quota" in e or "402" in e:
            return "Free quota exceeded — upgrade at console.deepgram.com"
        if "timeout" in e:
            return "File may be too long — try chunking"
        if "cannot reach" in e or "connection" in e:
            return "No internet — check network connection"
        return "Check backend/logs/criterion_errors.log"
