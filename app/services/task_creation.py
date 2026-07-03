from datetime import timedelta
from typing import Optional

from supabase import Client

from app.models.capture import ConfirmHierarchyItem


def _create_default_reminders(supabase: Client, task_id: str, due_at) -> None:
    """Same 60/30-minute default reminders as tasks.py's create_task —
    pulled out here so both places share one implementation instead of
    two copies that could quietly drift apart."""
    if not due_at:
        return
    reminder_rows = [
        {"task_id": task_id, "remind_at": (due_at - timedelta(minutes=60)).isoformat()},
        {"task_id": task_id, "remind_at": (due_at - timedelta(minutes=30)).isoformat()},
    ]
    supabase.table("task_reminder_preferences").insert(reminder_rows).execute()


def save_hierarchy_as_tasks(
    supabase: Client,
    user_id: str,
    hierarchy: list[ConfirmHierarchyItem],
    project_id: Optional[str],
    audience_id: Optional[str],
    location: Optional[str],
    source: str,
    voice_log_id: Optional[str] = None,
    image_log_id: Optional[str] = None,
    note_id: Optional[str] = None,
) -> list[dict]:
    """
    Creates one task row per item in the hierarchy (parents and their
    sub-tasks, flattened), applies the shared project/audience/location
    to all of them, auto-creates default reminders for anything with a
    due_at, and links every created task back to whichever source
    triggered it (voice_log_tasks / image_log_tasks / note_tasks —
    whichever of the three ids was actually passed).

    Returns every created task as a flat list — parents and sub-tasks
    together, matching what POST /v1/capture/confirm's contract promises.
    """
    created_tasks: list[dict] = []

    for parent in hierarchy:
        parent_row = {
            "user_id": user_id,
            "project_id": project_id,
            "audience_id": audience_id,
            "location": location,
            "label": parent.label,
            "base_urgency": parent.base_urgency,
            "urgency": parent.base_urgency,
            "escalation_enabled": True,
            "due_at": parent.due_at.isoformat() if parent.due_at else None,
            "source": source,
        }
        result = supabase.table("tasks").insert(parent_row).execute()
        parent_task = result.data[0]
        created_tasks.append(parent_task)
        _create_default_reminders(supabase, parent_task["id"], parent.due_at)

        for sub in parent.sub_tasks:
            sub_row = {
                "user_id": user_id,
                "project_id": project_id,
                "audience_id": audience_id,
                "parent_task_id": parent_task["id"],
                "location": location,
                "label": sub.label,
                "base_urgency": sub.base_urgency,
                "urgency": sub.base_urgency,
                "escalation_enabled": True,
                "due_at": sub.due_at.isoformat() if sub.due_at else None,
                "source": source,
            }
            sub_result = supabase.table("tasks").insert(sub_row).execute()
            sub_task = sub_result.data[0]
            created_tasks.append(sub_task)
            _create_default_reminders(supabase, sub_task["id"], sub.due_at)

    # Link every created task back to whatever triggered its creation.
    join_table, join_id = None, None
    if voice_log_id:
        join_table, join_id = "voice_log_tasks", ("voice_log_id", voice_log_id)
    elif image_log_id:
        join_table, join_id = "image_log_tasks", ("image_log_id", image_log_id)
    elif note_id:
        join_table, join_id = "note_tasks", ("note_id", note_id)

    if join_table and created_tasks:
        join_rows = [
            {join_id[0]: join_id[1], "task_id": t["id"]} for t in created_tasks
        ]
        supabase.table(join_table).insert(join_rows).execute()

    return created_tasks
