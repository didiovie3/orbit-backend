import json
import uuid
from datetime import datetime, timedelta, timezone

from supabase import Client

from app.core.config import get_settings
from app.db.supabase_client import execute_maybe_single
from app.services.calendar_service import (
    create_calendar_event,
    get_valid_access_token,
    list_calendar_events,
    register_webhook_channel,
    stop_webhook_channel,
    update_calendar_event,
)
from app.services.projects import get_unsorted_project_id

# Renew any channel expiring within this window — comfortably inside
# Google's 7-day channel max, so a job checking every few hours never
# actually lets one expire.
WEBHOOK_RENEWAL_WINDOW_HOURS = 24


def get_access_token(supabase: Client, user_id: str) -> str | None:
    """Returns a valid access token, refreshing and persisting a new one
    if the stored one has expired. Returns None if the user has no
    calendar connected at all."""
    user_result = execute_maybe_single(
        supabase.table("users").select("google_calendar_token").eq("id", user_id)
    )
    stored = user_result.data.get("google_calendar_token") if user_result.data else None
    if not stored:
        return None

    access_token, updated_json = get_valid_access_token(stored)
    if updated_json:
        supabase.table("users").update({"google_calendar_token": updated_json}).eq("id", user_id).execute()
    return access_token


def run_renew_webhooks_job(supabase: Client) -> dict:
    """
    Google's calendar push channels (registered in connect_calendar) expire
    on their own after at most 7 days — nothing else ever re-registers one,
    so without this job every webhook silently goes quiet a week after
    connecting. Manual sync and the on-open sync in CalendarScreen aren't
    affected either way (they don't depend on webhooks); this only keeps
    the push-triggered path alive. Runs on a schedule since there's no
    per-request trigger for it the way sync has.
    """
    settings = get_settings()
    if not settings.webhook_base_url:
        return {"checked": 0, "renewed": 0}

    users_result = (
        supabase.table("users")
        .select("id, google_calendar_token")
        .not_.is_("google_calendar_token", "null")
        .execute()
    )

    checked_count = 0
    renewed_count = 0
    for user in users_result.data:
        stored = user.get("google_calendar_token")
        if not stored:
            continue
        token_data = json.loads(stored)
        channel_id = token_data.get("channel_id")
        resource_id = token_data.get("resource_id")
        expiration_ms = token_data.get("channel_expiration")
        # No channel registered at all (e.g. connected before
        # webhook_base_url was configured) — nothing to renew here.
        if not channel_id or not resource_id or not expiration_ms:
            continue

        checked_count += 1
        expires_at = datetime.fromtimestamp(int(expiration_ms) / 1000, tz=timezone.utc)
        if expires_at - datetime.now(timezone.utc) > timedelta(hours=WEBHOOK_RENEWAL_WINDOW_HOURS):
            continue  # not close enough to expiring yet

        access_token = get_access_token(supabase, user["id"])
        if not access_token:
            continue

        stop_webhook_channel(access_token, channel_id, resource_id)

        # Channel ids can't be reused, so this registers a brand new one
        # rather than trying to extend the old one — Google's watch API
        # has no "extend" operation, only register/stop.
        new_channel_id = str(uuid.uuid4())
        try:
            channel = register_webhook_channel(
                access_token=access_token,
                channel_id=new_channel_id,
                channel_token=token_data["channel_token"],
                address=f"{settings.webhook_base_url.rstrip('/')}/v1/calendar/webhook",
            )
        except Exception:
            continue  # best-effort — stays expired until the next run picks it up again

        token_data["channel_id"] = new_channel_id
        token_data["resource_id"] = channel["resourceId"]
        token_data["channel_expiration"] = channel.get("expiration")
        supabase.table("users").update({"google_calendar_token": json.dumps(token_data)}).eq(
            "id", user["id"]
        ).execute()
        renewed_count += 1

    return {"checked": checked_count, "renewed": renewed_count}


def run_two_way_sync(supabase: Client, user_id: str) -> dict:
    access_token = get_access_token(supabase, user_id)
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

    # Only newly-created events count as "pushed" — matching pulled_count's
    # new-only semantics below. Existing linked events still get re-PATCHed
    # every run to pick up any local edits, but that's a routine keep-alive
    # touch, not a new sync, so it shouldn't inflate the count.
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

    # Create-fallback (not just a lookup) — same invariant as every other
    # task-creation path in this backend: "no project" always means the
    # real Unsorted project, never a null FK.
    unsorted_project_id = get_unsorted_project_id(supabase, user_id)

    pulled_count = 0
    for event in events:
        if event.get("status") == "cancelled":
            # Only worth acting on if it's an event we'd actually linked to
            # a task — an unknown cancelled event has nothing to move.
            if event["id"] in known_event_ids:
                handle_deleted_calendar_event(supabase, user_id, event["id"])
            continue

        if event["id"] in known_event_ids:
            continue

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
    unsorted_project_id = get_unsorted_project_id(supabase, user_id)

    supabase.table("tasks").update(
        {"project_id": unsorted_project_id, "calendar_event_id": None}
    ).eq("user_id", user_id).eq("calendar_event_id", calendar_event_id).execute()
