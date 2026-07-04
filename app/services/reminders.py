from datetime import datetime, timezone

from supabase import Client

from app.services.push_notifications import send_push_notification


def run_fire_reminders_job(supabase: Client) -> dict:
    """
    Finds every task_reminder_preferences row whose remind_at has passed,
    and for each one not already recorded in reminder_events, creates the
    firing record and attempts a push.

    Idempotent by design — running this twice in a row (or every minute,
    which is the real intent) won't double-fire the same reminder, since
    each one is checked against reminder_events (matched on task_id +
    scheduled_for) before anything happens.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    due_prefs = (
        supabase.table("task_reminder_preferences")
        .select("id, task_id, remind_at")
        .lte("remind_at", now_iso)
        .execute()
    )

    fired_count = 0
    pushed_count = 0
    skipped_already_fired = 0

    for pref in due_prefs.data:
        existing = (
            supabase.table("reminder_events")
            .select("id")
            .eq("task_id", pref["task_id"])
            .eq("scheduled_for", pref["remind_at"])
            .execute()
        )
        if existing.data:
            skipped_already_fired += 1
            continue

        task_result = (
            supabase.table("tasks")
            .select("id, user_id, label, due_at")
            .eq("id", pref["task_id"])
            .maybe_single()
            .execute()
        )
        task = task_result.data
        if not task:
            continue  # task was deleted after the reminder was scheduled

        # nudge_count reflects how many reminders have already fired for
        # this task — the second (closer-to-due-time) reminder naturally
        # becomes nudge 2, matching the PRD's "second reminder is more
        # intense" behavior without needing separate escalation logic here.
        prior_fires = (
            supabase.table("reminder_events")
            .select("id", count="exact")
            .eq("task_id", pref["task_id"])
            .execute()
        )
        nudge_count = (prior_fires.count or 0) + 1

        supabase.table("reminder_events").insert(
            {
                "task_id": task["id"],
                "scheduled_for": pref["remind_at"],
                "fired_at": now_iso,
                "nudge_count": nudge_count,
            }
        ).execute()
        fired_count += 1

        user_result = (
            supabase.table("users").select("fcm_token").eq("id", task["user_id"]).maybe_single().execute()
        )
        fcm_token = user_result.data.get("fcm_token") if user_result.data else None

        was_pushed = send_push_notification(
            fcm_token=fcm_token,
            title="Orbit reminder" if nudge_count == 1 else "Orbit reminder (2nd nudge)",
            body=task["label"],
        )
        if was_pushed:
            pushed_count += 1

    return {
        "checked": len(due_prefs.data),
        "fired": fired_count,
        "pushed": pushed_count,
        "already_fired": skipped_already_fired,
    }
