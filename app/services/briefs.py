from datetime import datetime, timedelta, timezone

from supabase import Client

from app.services.gemini_service import generate_eod_reflection


def _today_bounds() -> tuple[str, str]:
    """UTC calendar day — same simplification the rest of this backend
    already makes (escalation/reminder jobs run on a fixed schedule
    regardless of the user's actual timezone)."""
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


def _project_name_map(supabase: Client, user_id: str) -> dict[str, str]:
    result = supabase.table("projects").select("id, name").eq("owner_id", user_id).execute()
    return {row["id"]: row["name"] for row in result.data}


def _open_tasks(supabase: Client, user_id: str, statuses: list[str]) -> list[dict]:
    result = (
        supabase.table("tasks")
        .select("id, label, project_id, status, urgency, updated_at")
        .eq("user_id", user_id)
        .in_("status", statuses)
        .is_("archived_at", "null")
        .execute()
    )
    return result.data


def _done_today(supabase: Client, user_id: str) -> list[dict]:
    start, end = _today_bounds()
    result = (
        supabase.table("tasks")
        .select("id, label, updated_at")
        .eq("user_id", user_id)
        .eq("status", "done")
        .gte("updated_at", start)
        .lt("updated_at", end)
        .execute()
    )
    return result.data


def compute_current_streak(supabase: Client, user_id: str) -> int:
    """Consecutive days up to and including today with at least one task
    touched. Deliberately simpler than the Android client's on-device
    heuristic (which tolerates short gaps) — this is a separate, honest
    server-side approximation, not guaranteed to match the number shown
    in the app exactly."""
    result = supabase.table("tasks").select("updated_at").eq("user_id", user_id).execute()
    active_days = {row["updated_at"][:10] for row in result.data}
    streak = 0
    cursor = datetime.now(timezone.utc).date()
    while cursor.isoformat() in active_days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def generate_morning_brief_content(supabase: Client, user_id: str) -> str:
    projects = _project_name_map(supabase, user_id)
    overdue = _open_tasks(supabase, user_id, ["overdue"])
    due_today = _open_tasks(supabase, user_id, ["today"])

    if not overdue and not due_today:
        return "Nothing overdue and nothing due today — a clean slate to start the day."

    counts_by_project: dict[str | None, int] = {}
    for t in overdue + due_today:
        counts_by_project[t["project_id"]] = counts_by_project.get(t["project_id"], 0) + 1
    heaviest_id = max(counts_by_project, key=counts_by_project.get)
    heaviest_name = projects.get(heaviest_id) if heaviest_id else "Unsorted"

    if overdue and due_today:
        headline = f"{len(overdue)} overdue, {len(due_today)} due today — heaviest load is {heaviest_name}."
    elif overdue:
        headline = f"{len(overdue)} overdue, nothing new due today — heaviest load is {heaviest_name}."
    else:
        headline = f"{len(due_today)} due today, nothing overdue — heaviest load is {heaviest_name}."

    lines = [headline]
    if overdue:
        shown = "; ".join(t["label"] for t in overdue[:5])
        lines.append(f"Overdue: {shown}" + ("…" if len(overdue) > 5 else "."))
    if due_today:
        shown = "; ".join(t["label"] for t in due_today[:5])
        lines.append(f"Due today: {shown}" + ("…" if len(due_today) > 5 else "."))
    return " ".join(lines)


def generate_progress_brief_content(supabase: Client, user_id: str) -> str:
    open_tasks = _open_tasks(supabase, user_id, ["today", "overdue"])
    done_today = _done_today(supabase, user_id)
    total = len(open_tasks) + len(done_today)

    if total == 0:
        return "Nothing scheduled for today yet."

    headline = f"{len(done_today)} of {total} done so far today."
    still_overdue = [t for t in open_tasks if t["status"] == "overdue"]
    if still_overdue:
        return f"{headline} {still_overdue[0]['label']} is still untouched and overdue."

    remaining = sorted(open_tasks, key=lambda t: -t["urgency"])
    if remaining:
        return f"{headline} Highest-urgency remaining: {remaining[0]['label']}."
    return f"{headline} Everything else is done too."


def generate_eod_brief_content(supabase: Client, user_id: str) -> str:
    done_today = _done_today(supabase, user_id)
    carried_forward = _open_tasks(supabase, user_id, ["today", "overdue"])
    streak = compute_current_streak(supabase, user_id)

    stats = (
        f"{len(done_today)} completed today, {len(carried_forward)} carried forward to tomorrow. "
        f"Current streak: {streak} day{'s' if streak != 1 else ''}."
    )

    try:
        reflection = generate_eod_reflection(
            done_labels=[t["label"] for t in done_today],
            carried_count=len(carried_forward),
            streak=streak,
        )
    except Exception:
        # Gemini being unavailable shouldn't block the brief entirely —
        # the real numbers above are still worth having on their own.
        reflection = ""

    return f"{stats} {reflection}".strip()
