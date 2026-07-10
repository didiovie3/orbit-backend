from functools import lru_cache
from types import SimpleNamespace
from typing import Any

from supabase import Client, create_client

from app.core.config import get_settings


@lru_cache
def get_supabase() -> Client:
    """
    Service-role Supabase client. Bypasses RLS by design — the backend is
    trusted, and every query below this must manually filter by
    current_user.user_id (RLS won't do it for you here like it does for
    the Kotlin client's anon-key connection).
    """
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_service_key)


def execute_maybe_single(query_builder: Any) -> Any:
    """
    Call as `execute_maybe_single(supabase.table(...).select(...)...)` in
    place of `.maybe_single().execute()`.

    The installed postgrest-py (2.31.0) returns None outright — not an
    object with `.data = None` — when a maybe_single() query matches zero
    rows. Every "not found" check in this codebase is written as
    `if not result.data`, which crashes with AttributeError on that bare
    None. This normalizes it back to a real object with `.data = None` so
    those checks (and the 404s they raise) actually work.
    """
    result = query_builder.maybe_single().execute()
    if result is None:
        return SimpleNamespace(data=None, count=None)
    return result
