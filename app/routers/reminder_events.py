from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from supabase import Client

from app.core.auth import CurrentUser, get_current_user
from app.db.supabase_client import execute_maybe_single, get_supabase
from app.models.reminder_event import ReminderEventResponse, ReminderEventUpdate

router = APIRouter(prefix="/reminder-events", tags=["reminder-events"])


def _get_owned_reminder_event(supabase: Client, event_id: UUID, user_id: str) -> dict:
    """
    reminder_events has no user_id column of its own — ownership only
    exists indirectly, through the task it's attached to. Same two-query
    shape as every other ownership check in this backend, just one hop
    further out.
    """
    result = execute_maybe_single(
        supabase.table("reminder_events").select("*").eq("id", str(event_id))
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reminder event not found")

    task_result = execute_maybe_single(
        supabase.table("tasks").select("user_id").eq("id", result.data["task_id"])
    )
    if not task_result.data or task_result.data["user_id"] != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reminder event not found")

    return result.data


@router.patch("/{event_id}", response_model=ReminderEventResponse)
def update_reminder_event(
    event_id: UUID,
    body: ReminderEventUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    _get_owned_reminder_event(supabase, event_id, current_user.user_id)

    updates = body.model_dump(exclude_unset=True)
    if updates.get("rescheduled_to") is not None:
        updates["rescheduled_to"] = body.rescheduled_to.isoformat()

    if updates:
        supabase.table("reminder_events").update(updates).eq("id", str(event_id)).execute()

    result = execute_maybe_single(supabase.table("reminder_events").select("*").eq("id", str(event_id)))
    return result.data
