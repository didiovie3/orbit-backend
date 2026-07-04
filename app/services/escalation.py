from datetime import datetime, timezone

from supabase import Client

# Same thresholds as the SQL version in the database guide — kept here as
# a Python translation instead of a Postgres function specifically so it
# can run through our existing fake-Supabase test harness the same way
# every other piece of logic in this backend has been tested.
ESCALATION_THRESHOLDS = [
    # (hours_remaining_below, minimum_urgency)
    (0, 5),      # overdue — forced to 5 regardless of base_urgency
    (6, 5),      # under 6 hours
    (24, 4),
    (72, 4),
    (168, 3),    # under 7 days
]


def compute_effective_urgency(due_at: datetime, base_urgency: int) -> int:
    """
    Escalation only ever RAISES urgency, never lowers it — base_urgency
    is the floor this compares against, never overridden downward.
    """
    now = datetime.now(timezone.utc)
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)

    hours_remaining = (due_at - now).total_seconds() / 3600

    for threshold_hours, min_urgency in ESCALATION_THRESHOLDS:
        if hours_remaining < threshold_hours:
            return max(base_urgency, min_urgency)

    return base_urgency  # more than 7 days out — no escalation yet


def run_escalation_job(supabase: Client) -> dict:
    """
    Runs across ALL users' tasks — this is a system-wide background job,
    not scoped to one request's user_id the way every endpoint so far has
    been. That's a real, deliberate difference worth noticing: this is
    the first piece of code in the backend that isn't triggered by
    someone tapping something in the app.

    Returns a small summary dict rather than the updated rows — this is
    meant to be called on a timer, not inspected row-by-row each time.
    """
    result = (
        supabase.table("tasks")
        .select("id, base_urgency, urgency, due_at")
        .neq("status", "done")
        .is_("archived_at", "null")
        .eq("escalation_enabled", True)
        .not_.is_("due_at", "null")
        .execute()
    )

    updated_count = 0
    for task in result.data:
        due_at = datetime.fromisoformat(task["due_at"].replace("Z", "+00:00"))
        new_urgency = compute_effective_urgency(due_at, task["base_urgency"])
        if new_urgency != task["urgency"]:
            supabase.table("tasks").update({"urgency": new_urgency}).eq(
                "id", task["id"]
            ).execute()
            updated_count += 1

    return {"checked": len(result.data), "updated": updated_count}


def run_overdue_flip_job(supabase: Client) -> dict:
    """
    Separate from escalation on purpose — this changes status, escalation
    only ever changes urgency. A task can be at urgency 5 for days before
    its due date without being overdue; the moment due_at passes is a
    distinct event from "this is very urgent."
    """
    now = datetime.now(timezone.utc).isoformat()
    result = (
        supabase.table("tasks")
        .select("id")
        .lt("due_at", now)
        .in_("status", ["today", "later"])
        .is_("archived_at", "null")
        .execute()
    )

    for task in result.data:
        supabase.table("tasks").update({"status": "overdue"}).eq("id", task["id"]).execute()

    return {"flipped_to_overdue": len(result.data)}
