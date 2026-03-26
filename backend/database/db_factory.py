"""
database/db_factory.py
───────────────────────
Auto-selects the right database backend:

  Supabase → if SUPABASE_URL is a real URL + supabase-py is installed
  SQLite   → fallback (zero config, local/dev)
"""
import os


def _supabase_ready() -> bool:
    """True only if package is installed AND env vars look real (not placeholders)."""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_ANON_KEY", "")
    if not url or not key:
        return False
    # Reject obvious placeholder values
    if "xxxxxxxxxxxx" in url or "your_" in url.lower() or "..." in key:
        return False
    if len(key) < 40:
        return False
    try:
        import supabase  # noqa: F401
        return True
    except ImportError:
        return False


def get_db():
    """Return the appropriate database module (same interface for both)."""
    if _supabase_ready():
        from database import supabase_db as _db
        print("[DB] Using Supabase PostgreSQL")
        return _db
    from database import db as _db
    print("[DB] Using SQLite (local)")
    return _db


_instance = None

def db():
    """Cached singleton."""
    global _instance
    if _instance is None:
        _instance = get_db()
    return _instance
