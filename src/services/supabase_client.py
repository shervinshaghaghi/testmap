from __future__ import annotations

import os

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

_client: Client | None = None


def get_supabase() -> Client:
    global _client

    if _client is not None:
        return _client

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")

    if not url:
        raise RuntimeError("Missing SUPABASE_URL environment variable.")
    if not key:
        raise RuntimeError("Missing SUPABASE_KEY environment variable.")

    _client = create_client(url, key)
    return _client