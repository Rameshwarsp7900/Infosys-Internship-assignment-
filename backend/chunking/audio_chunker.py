"""
audio_chunker.py — v3 fixed
─────────────────────────────
Key fix: ffmpeg not required for files under 10 minutes.
For files under 10min (the vast majority of support calls),
we skip pydub entirely and send the original file to Deepgram.

pydub + ffmpeg is only needed when splitting files > 10min.
This eliminates the WinError / ffmpeg-not-found warning for normal calls.
"""
import os, shutil, uuid, logging
from pathlib import Path
from typing  import List, Dict

log = logging.getLogger(__name__)

CHUNK_DURATION_MS  = 600_000   # 10 minutes — only chunk if longer
OVERLAP_MS         = 2_000     # 2s overlap to avoid word cutoff at boundaries
MIN_CHUNK_MS       = 5_000     # ignore tiny tail chunks under 5s


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None or shutil.which("ffmpeg.exe") is not None


def should_chunk(file_path: str) -> bool:
    """
    Returns True only if the file is longer than CHUNK_DURATION_MS
    AND ffmpeg is available to perform the split.
    
    If ffmpeg is not installed, always return False — Deepgram handles
    files up to 2GB natively without local splitting.
    """
    if not _ffmpeg_available():
        # No ffmpeg — let Deepgram handle the full file
        # Deepgram's API accepts files up to 2GB, no local splitting needed
        return False

    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_file(file_path)
        return len(audio) > CHUNK_DURATION_MS
    except Exception as e:
        log.debug(f"should_chunk: cannot read {file_path}: {e} — skipping chunking")
        return False


def chunk_audio(file_path: str, output_dir: str) -> List[Dict]:
    """
    Split audio into overlapping chunks of CHUNK_DURATION_MS.
    Only called when should_chunk() returns True (requires ffmpeg).
    """
    if not _ffmpeg_available():
        raise RuntimeError(
            "ffmpeg is not installed. Install from https://ffmpeg.org/download.html "
            "and add it to your PATH. Alternatively, Deepgram handles files up to 2GB "
            "without chunking — ensure should_chunk() returns False."
        )

    try:
        from pydub import AudioSegment
    except ImportError:
        raise RuntimeError("pydub not installed. Run: pip install pydub")

    audio     = AudioSegment.from_file(file_path)
    total_ms  = len(audio)
    ext       = Path(file_path).suffix.lower() or ".wav"
    out_dir   = Path(output_dir) / f"chunks_{uuid.uuid4().hex[:8]}"
    out_dir.mkdir(parents=True, exist_ok=True)

    chunks = []
    start  = 0
    idx    = 0

    while start < total_ms:
        end  = min(start + CHUNK_DURATION_MS + OVERLAP_MS, total_ms)
        seg  = audio[start:end]

        if len(seg) < MIN_CHUNK_MS and idx > 0:
            break  # skip tiny tail

        out_path = str(out_dir / f"chunk_{idx:04d}{ext}")
        seg.export(out_path, format=ext.lstrip("."))

        chunks.append({
            "chunk_idx": idx,
            "path":      out_path,
            "start_ms":  start,
            "end_ms":    end,
        })
        log.info(f"Chunk {idx}: {start//1000}s → {end//1000}s → {out_path}")

        start += CHUNK_DURATION_MS   # move forward without overlap for start position
        idx   += 1

    return chunks


def merge_transcripts(chunk_results: List[Dict]) -> Dict:
    """Merge chunk transcripts back into a single transcript, adjusting timestamps."""
    sorted_chunks = sorted(chunk_results, key=lambda x: x["chunk_idx"])
    all_segments  = []
    full_text_parts = []

    for item in sorted_chunks:
        tr        = item["transcript"]
        offset_s  = item["start_ms"] / 1000.0
        for seg in tr.get("segments", []):
            adjusted = dict(seg)
            adjusted["start"] = round(seg.get("start", 0) + offset_s, 2)
            adjusted["end"]   = round(seg.get("end",   0) + offset_s, 2)
            all_segments.append(adjusted)
        if tr.get("full_text"):
            full_text_parts.append(tr["full_text"])

    # Re-merge consecutive same-speaker segments across chunk boundaries
    # (chunks can end/start mid-turn, creating false speaker alternations)
    if all_segments:
        merged = [all_segments[0]]
        for seg in all_segments[1:]:
            last = merged[-1]
            if seg.get("speaker") == last.get("speaker"):
                last["text"] += " " + seg["text"]
                last["end"]   = seg["end"]
            else:
                merged.append(seg)
        all_segments = merged

    return {
        "segments":   all_segments,
        "full_text":  " ".join(full_text_parts),
        "word_count": sum(len(p.split()) for p in full_text_parts),
        "duration":   max((s.get("end", 0) for s in all_segments), default=0),
        "language":   sorted_chunks[-1]["transcript"].get("language", "en") if sorted_chunks else "en",
        "speakers":   len(set(s.get("speaker","") for s in all_segments)),
    }


def cleanup_chunks(chunks: List[Dict]):
    """Remove temporary chunk files and their directory."""
    if not chunks:
        return
    try:
        chunk_dir = Path(chunks[0]["path"]).parent
        for ch in chunks:
            try:
                Path(ch["path"]).unlink(missing_ok=True)
            except Exception:
                pass
        try:
            chunk_dir.rmdir()
        except Exception:
            pass
    except Exception as e:
        log.debug(f"cleanup_chunks: {e}")
