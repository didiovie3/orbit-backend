from supabase import Client

from app.db.supabase_client import execute_maybe_single

UNSORTED_COLOR = "#8B8FA8"


def get_unsorted_project_id(supabase: Client, user_id: str) -> str:
    """Every user gets a real, permanent 'Unsorted' project row (created by
    the handle_new_user signup trigger) — tasks/notes created without an
    explicit project point here instead of leaving project_id null, so
    "no project" behaves identically to a real project everywhere (map
    position, filter chips, calendar_sync's moved-to-unsorted writes) with
    no special-casing needed. The insert-if-missing fallback below only
    matters for accounts that predate that trigger."""
    result = execute_maybe_single(
        supabase.table("projects")
        .select("id")
        .eq("owner_id", user_id)
        .eq("is_unsorted", True)
    )
    if result.data:
        return result.data["id"]

    created = (
        supabase.table("projects")
        .insert({"owner_id": user_id, "name": "Unsorted", "color": UNSORTED_COLOR, "is_unsorted": True})
        .execute()
    )
    return created.data[0]["id"]


def is_owned_project(supabase: Client, project_id: str, user_id: str) -> bool:
    """Whether project_id both exists and belongs to user_id. Every place a
    task/note/time-block can be tagged with a project_id needs this check —
    the service-role client bypasses RLS, so without it a client could
    attach its own rows to another user's project just by guessing/observing
    its id, same class of gap as audiences.is_owned_audience."""
    result = execute_maybe_single(
        supabase.table("projects").select("id").eq("id", project_id).eq("owner_id", user_id)
    )
    return result.data is not None
