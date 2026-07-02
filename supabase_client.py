from functools import lru_cache

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
