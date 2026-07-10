from supabase import Client

from app.db.supabase_client import execute_maybe_single


def is_owned_audience(supabase: Client, audience_id: str, user_id: str) -> bool:
    """Whether audience_id both exists and belongs to user_id. Every place a
    task can be tagged with an audience_id (create_task, update_task, the
    capture/confirm flow) needs this check — the service-role client bypasses
    RLS, so without it a client could link a task to an audience it doesn't
    own just by guessing/observing another user's audience id."""
    result = execute_maybe_single(
        supabase.table("audiences").select("id").eq("id", audience_id).eq("user_id", user_id)
    )
    return result.data is not None
