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
            .select("id, user_id, label, due_at, project_id, location, status")
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
        reminder_event_id = reminder_event_result.data[0]["id"]
        fired_count += 1

        user_result = (
            supabase.table("users").select("fcm_token").eq("id", task["user_id"]).maybe_single().execute()
        )
        fcm_token = user_result.data.get("fcm_token") if user_result.data else None

        project_name = "Unsorted"
        if task.get("project_id"):
            project_result = (
                supabase.table("projects").select("name").eq("id", task["project_id"]).maybe_single().execute()
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
