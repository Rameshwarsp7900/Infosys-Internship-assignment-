"""
file_detector.py — v3 fixed
────────────────────────────
Bug fixes:
  1. detect_and_process() now accepts call_id and passes it to Deepgram
  2. file_type() treats 'unknown' as 'audio' when extension is in AUDIO_EXTENSIONS
     (was returning "unknown" instead of "audio" for valid audio files)
"""

import os
from typing import Optional
from transcription.deepgram_processor import DeepgramProcessor
from transcription.chat_processor      import ChatProcessor

AUDIO_EXTENSIONS = {"mp3", "m4a", "wav", "ogg", "flac", "webm", "aac"}
TEXT_EXTENSIONS  = {"txt", "log", "csv"}


class FileDetector:
    def __init__(self, api_key: Optional[str] = None):
        self._api_key  = api_key or os.getenv("DEEPGRAM_API_KEY", "")
        self._deepgram = None
        self._chat     = ChatProcessor()

    def _get_deepgram(self) -> DeepgramProcessor:
        if self._deepgram is None:
            self._deepgram = DeepgramProcessor(self._api_key)
        return self._deepgram

    # ── FIX 1: pass call_id through to Deepgram ──────────────────────────
    def detect_and_process(self, file_path: str, call_id: str = "unknown") -> dict:
        ext = self._ext(file_path)
        if ext in AUDIO_EXTENSIONS:
            return self._get_deepgram().transcribe(file_path, call_id=call_id)
        if ext in TEXT_EXTENSIONS:
            return self._chat.process(file_path)
        raise ValueError(
            f"Unsupported file type '.{ext}'. "
            f"Supported: {', '.join(sorted(AUDIO_EXTENSIONS | TEXT_EXTENSIONS))}"
        )

    # ── FIX 2: never return "unknown" for valid audio extensions ─────────
    def file_type(self, file_path: str) -> str:
        ext = self._ext(file_path)
        if ext in AUDIO_EXTENSIONS:
            return "audio"
        if ext in TEXT_EXTENSIONS:
            return "chat"
        # Still return unknown for genuinely unsupported types
        return "unknown"

    @staticmethod
    def _ext(file_path: str) -> str:
        return file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""

    @staticmethod
    def get_supported_formats():
        return sorted(AUDIO_EXTENSIONS | TEXT_EXTENSIONS)
