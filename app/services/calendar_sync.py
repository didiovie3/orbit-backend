from datetime import datetime, timezone

from supabase import Client

from app.services.calendar_service import (
    create_calendar_event,
    get_valid_access_token,
    list_calendar_events,
    update_calendar_event,
)


def _get_access_token(supabase: Client, user_id: str) -> str | None:
    """Returns a valid access token, refreshing and persisting a new one
    if the stored one has expired. Returns None if the user has no
    calendar connected at all."""
    user_result = (
        supabase.table("users").select("google_calendar_token").eq("id", user_id).maybe_single().execute()
    )
    stored = user_result.data.get("google_calendar_token") if user_result.data else None
    if not stored:
        return None

    access_token, updated_json = get_valid_access_token(stored)
    if updated_json:
        supabase.table("users").update({"google_calendar_token": updated_json}).eq("id", user_id).execute()
    return access_token


def run_two_way_sync(supabase: Client, user_id: str) -> dict:
    access_token = _get_access_token(supabase, user_id)
    if not access_token:
        return {"pushed": 0, "pulled": 0, "synced_at": datetime.now(timezone.utc).isoformat(), "connected": False}

    pushed = _push_local_tasks(supabase, user_id, access_token)
    pulled = _pull_calendar_events(supabase, user_id, access_token)

    return {
        "pushed": pushed,
        "pulled": pulled,
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "connected": True,
    }


def _push_local_tasks(supabase: Client, user_id: str, access_token: str) -> int:
    """Pushes any task with a due_at set. New ones (no calendar_event_id
    yet) get created; already-linked ones get updated — cheap and
    idempotent even when nothing actually changed."""
    tasks_result = (
        supabase.table("tasks")
        .select("id, label, due_at, calendar_event_id")
        .eq("user_id", user_id)
        .is_("archived_at", "null")
        .not_.is_("due_at", "null")
        .execute()
    )

    pushed_count = 0
    for task in tasks_result.data:
        if task["calendar_event_id"]:
            update_calendar_event(access_token, task["calendar_event_id"], task["label"], task["due_at"])
        else:
            event_id = create_calendar_event(access_token, task["label"], task["due_at"])
            supabase.table("tasks").update({"calendar_event_id": event_id}).eq("id", task["id"]).execute()
        pushed_count += 1

    return pushed_count


def _pull_calendar_events(supabase: Client, user_id: str, access_token: str) -> int:
    """Any calendar event not already linked to a task becomes a new
    task in the Unsorted bucket — the user triages it from there, same
    as any other unsorted capture."""
    events = list_calendar_events(access_token)

    existing_result = (
        supabase.table("tasks")
        .select("calendar_event_id")
        .eq("user_id", user_id)
        .not_.is_("calendar_event_id", "null")
        .execute()
    )
    known_event_ids = {t["calendar_event_id"] for t in existing_result.data}

    unsorted_result = (
        supabase.table("projects")
        .select("id")
        .eq("owner_id", user_id)
        .eq("is_unsorted", True)
        .maybe_single()
        .execute()
    )
    unsorted_project_id = unsorted_result.data["id"] if unsorted_result.data else None

    pulled_count = 0
    for event in events:
        if event["id"] in known_event_ids:
            continue
        if event.get("status") == "cancelled":
            continue  # a deleted event we never knew about — nothing to move to unsorted

        due_at = event.get("start", {}).get("dateTime")
        if not due_at:
            continue  # all-day events with no specific time aren't a fit for a task's due_at

        supabase.table("tasks").insert(
            {
                "user_id": user_id,
                "project_id": unsorted_project_id,
                "label": event.get("summary", "(untitled calendar event)"),
                "due_at": due_at,
                "calendar_event_id": event["id"],
                "base_urgency": 2,
                "urgency": 2,
                "source": "typed",
            }
        ).execute()
        pulled_count += 1

    return pulled_count


def handle_deleted_calendar_event(supabase: Client, user_id: str, calendar_event_id: str) -> None:
    """
    Per the contract: if a calendar event is deleted, the matching task
    moves to unsorted rather than being deleted. Called by the webhook
    handler once it detects a cancelled event during its pull.
    """
    unsorted_result = (
        supabase.table("projects")
        .select("id")
        .eq("owner_id", user_id)
        .eq("is_unsorted", True)
        .maybe_single()
        .execute()
    )
    unsorted_project_id = unsorted_result.data["id"] if unsorted_result.data else None

    supabase.table("tasks").update(
        {"project_id": unsorted_project_id, "calendar_event_id": None}
    ).eq("user_id", user_id).eq("calendar_event_id", calendar_event_id).execute()
