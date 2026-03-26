"""
rag/pdf_processor.py
─────────────────────
Extract text from PDF policy documents.

Uses pypdf (pure Python, no system dependencies).
Install: pip install pypdf

Falls back gracefully if pypdf is not available.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_path: str) -> Optional[str]:
    """
    Extract all text from a PDF file.
    Returns plain text string, or None on failure.
    """
    try:
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        pages  = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text.strip())
        full_text = "\n\n".join(pages)
        logger.info(f"[PDF] Extracted {len(pages)} pages, {len(full_text)} chars from {file_path}")
        return full_text if full_text.strip() else None
    except ImportError:
        logger.warning("[PDF] pypdf not installed. Run: pip install pypdf")
        return _fallback_text_extract(file_path)
    except Exception as e:
        logger.error(f"[PDF] Extraction failed for {file_path}: {e}")
        return None


def _fallback_text_extract(file_path: str) -> Optional[str]:
    """
    Minimal fallback: try reading as plain text (works for text-based PDFs
    that are just encoded ASCII, not scanned images).
    """
    try:
        with open(file_path, "rb") as f:
            raw = f.read()
        # Extract text-like content between PDF stream markers
        import re
        # Find all BT...ET text blocks
        texts = re.findall(rb"BT\s*(.*?)\s*ET", raw, re.DOTALL)
        result = []
        for block in texts:
            # Extract string literals
            strings = re.findall(rb"\((.*?)\)", block)
            for s in strings:
                try:
                    result.append(s.decode("latin-1").strip())
                except Exception:
                    pass
        text = " ".join(result)
        return text if text.strip() else None
    except Exception as e:
        logger.error(f"[PDF] Fallback extraction failed: {e}")
        return None


def is_pdf(filename: str) -> bool:
    return filename.lower().endswith(".pdf")
