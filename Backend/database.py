# database.py – Supabase client (replaces SQLite/SQLAlchemy)

import os
from supabase import create_client

_client = None


def get_supabase():
    """Return Supabase client. Uses SUPABASE_URL and SUPABASE_KEY from env.
    For full backend access (bypass RLS), use the service_role key in .env if needed."""
    global _client
    if _client is None:
        url = (os.getenv("SUPABASE_URL") or "").strip()
        key = (os.getenv("SUPABASE_KEY") or "").strip()
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in .env")
        _client = create_client(url, key)
    return _client
