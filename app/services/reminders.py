from datetime import datetime, timezone

from postgrest.exceptions import APIError
from supabase import Client

from app.db.supabase_client import execute_maybe_single
from app.services.push_notifications import send_push_notification

# Postgres SQLSTATE for a unique-constraint violation — reminder_events has
# a UNIQUE(task_id, scheduled_for) constraint (see the
# add_reminder_events_task_scheduled_unique migration) backing the
# insert-conflict check below.
UNIQUE_VIOLATION_CODE = "23505"


def run_fire_reminders_job(supabase: Client) -> dict:
    """
    Finds every task_reminder_preferences row whose remind_at has passed,
    and for each one not already recorded in reminder_events, creates the
    firing record and attempts a push.

    The upfront `existing` check below is just a cheap fast path — it isn't
    atomic with the insert, so two overlapping runs of this job (e.g. a
    slow run still going when the next minute's fires, or a manual
    /debug/fire-reminders overlapping the scheduled one) could both pass it
    before either inserts. The UNIQUE(task_id, scheduled_for) constraint on
    reminder_events is what actually guarantees only one fire ever "wins" —
    the insert below catches the resulting conflict and treats it as
    "someone else already fired this" instead of double-pushing.
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

        task_result = execute_maybe_single(
            supabase.table("tasks")
            .select("id, user_id, label, due_at, project_id, location, status")
            .eq("id", pref["task_id"])
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

        try:
            reminder_event_result = (
                supabase.table("reminder_events")
                .insert(
                    {
                        "task_id": task["id"],
                        "scheduled_for": pref["remind_at"],
                        "fired_at": now_iso,
                        "nudge_count": nudge_count,
                    }
                )
                .execute()
            )
        except APIError as exc:
            if exc.code == UNIQUE_VIOLATION_CODE:
                # A concurrent run already inserted this exact
                # (task_id, scheduled_for) between our fast-path check
                # above and this insert — it already handled the push.
                skipped_already_fired += 1
                continue
            raise
        reminder_event_id = reminder_event_result.data[0]["id"]
        fired_count += 1

        user_result = execute_maybe_single(
            supabase.table("users").select("fcm_token").eq("id", task["user_id"])
        )
        fcm_token = user_result.data.get("fcm_token") if user_result.data else None

        project_name = "Unsorted"
        if task.get("project_id"):
            project_result = execute_maybe_single(
                supabase.table("projects").select("name").eq("id", task["project_id"])
            )
            if project_result.data:
                project_name = project_result.data["name"]

        due_label = ""
        if task.get("due_at"):
            try:
                due_dt = datetime.fromisoformat(task["due_at"].replace("Z", "+00:00"))
                due_label = due_dt.strftime("%I:%M %p").lstrip("0")
            except ValueError:
                due_label = ""

        is_escalated = nudge_count > 1  # the closer-to-due-time reminder, per the PRD's "more intense" second nudge
        is_overdue = task.get("status") == "overdue"

        was_pushed = send_push_notification(
            fcm_token=fcm_token,
            title="Orbit reminder" if nudge_count == 1 else "Orbit reminder (2nd nudge)",
            body=task["label"],
            data={
                "task_id": task["id"],
                "task_label": task["label"],
                "task_project": project_name,
                "task_location": task.get("location") or "",
                "task_due": due_label,
                "escalated": "true" if is_escalated else "false",
                "overdue": "true" if is_overdue else "false",
                "reminder_event_id": reminder_event_id,
            },
        )
        if was_pushed:
            pushed_count += 1

    return {
        "checked": len(due_prefs.data),
        "fired": fired_count,
        "pushed": pushed_count,
        "already_fired": skipped_already_fired,
    }
