from datetime import date, datetime, timezone

from supabase import Client

# A gap of this many days with no task activity breaks the streak back to
# zero. Shorter lapses (a day or two) don't reset it — this is the same
# gap-tolerant policy the Android app used to compute client-side; now it's
# the single canonical algorithm so the app and the EOD brief text always
# agree on the number.
STREAK_BREAK_GAP_DAYS = 3


def compute_streak(supabase: Client, user_id: str) -> tuple[int, bool]:
    """Returns (current_streak, active_today)."""
    result = supabase.table("tasks").select("updated_at").eq("user_id", user_id).execute()
    active_days = sorted({row["updated_at"][:10] for row in result.data})

    streak = 0
    last_active: date | None = None
    for day_str in active_days:
        day = datetime.fromisoformat(day_str).date()
        if last_active is not None:
            gap = (day - last_active).days
            streak = 1 if gap >= STREAK_BREAK_GAP_DAYS else streak + 1
        else:
            streak = 1
        last_active = day

    today = datetime.now(timezone.utc).date()
    active_today = last_active == today
    if last_active is not None and (today - last_active).days >= STREAK_BREAK_GAP_DAYS:
        streak = 0

    return streak, active_today
