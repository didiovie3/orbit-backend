from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from supabase import Client

from app.core.auth import CurrentUser, get_current_user
from app.db.supabase_client import get_supabase
from app.models.common import DeleteConfirm
from app.models.task import (
    TaskCreate,
    TaskListResponse,
    TaskResponse,
    TaskUpdate,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _get_owned_task(supabase: Client, task_id: UUID, user_id: str) -> dict:
    """
    Shared by every endpoint below that operates on a single task. Since
    we're using the service-role Supabase client (bypasses Row Level
    Security by design — see supabase_client.py), WE are responsible for
    checking the task actually belongs to the requesting user, not the
    database. Forgetting this check on any one endpoint would let any
    logged-in user read or modify anyone else's tasks by guessing IDs.
    """
    result = (
        supabase.table("tasks")
        .select("*")
        .eq("id", str(task_id))
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return result.data


@router.get("", response_model=TaskListResponse)
def list_tasks(
    project_id: UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    include_archived: bool = Query(default=False),
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    query = supabase.table("tasks").select("*").eq("user_id", current_user.user_id)

    if project_id is not None:
        query = query.eq("project_id", str(project_id))
    if status_filter is not None:
        query = query.eq("status", status_filter)
    if not include_archived:
        query = query.is_("archived_at", "null")

    result = query.order("urgency", desc=True).execute()
    return {"tasks": result.data}


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    body: TaskCreate,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    row = {
        "user_id": current_user.user_id,
        "project_id": str(body.project_id) if body.project_id else None,
        "parent_task_id": str(body.parent_task_id) if body.parent_task_id else None,
        "audience_id": str(body.audience_id) if body.audience_id else None,
        "label": body.label,
        "location": body.location,
        "base_urgency": body.base_urgency,
        "urgency": body.base_urgency,  # starts equal to base_urgency; escalation job raises it later
        "escalation_enabled": body.escalation_enabled,
        "due_at": body.due_at.isoformat() if body.due_at else None,
        "source": body.source,
    }
    result = supabase.table("tasks").insert(row).execute()
    task = result.data[0]

    # Per the API contract: if due_at is set, auto-create the two default
    # reminders (60 and 30 minutes before) unless the app already sent its
    # own via a separate POST /v1/tasks/:id/reminders call. That endpoint
    # doesn't exist yet (next thing to build after this), so for now this
    # always applies the defaults whenever due_at is present.
    if body.due_at:
        due = body.due_at
        reminder_rows = [
            {"task_id": task["id"], "remind_at": (due - timedelta(minutes=60)).isoformat()},
            {"task_id": task["id"], "remind_at": (due - timedelta(minutes=30)).isoformat()},
        ]
        supabase.table("task_reminder_preferences").insert(reminder_rows).execute()

    return task


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    return _get_owned_task(supabase, task_id, current_user.user_id)


@router.patch("/{task_id}", response_model=TaskResponse)
def update_task(
    task_id: UUID,
    body: TaskUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    _get_owned_task(supabase, task_id, current_user.user_id)  # 404s if not owned

    # exclude_unset=True is the key piece here — it only includes fields
    # the caller actually sent, so a PATCH that only sends {"label": "x"}
    # doesn't accidentally overwrite every other field with None.
    updates = body.model_dump(exclude_unset=True, exclude_none=False)
    if "due_at" in updates and updates["due_at"] is not None:
        updates["due_at"] = body.due_at.isoformat()
    for uuid_field in ("project_id", "parent_task_id", "audience_id"):
        if uuid_field in updates and updates[uuid_field] is not None:
            updates[uuid_field] = str(updates[uuid_field])

    if not updates:
        return _get_owned_task(supabase, task_id, current_user.user_id)

    result = (
        supabase.table("tasks")
        .update(updates)
        .eq("id", str(task_id))
        .eq("user_id", current_user.user_id)
        .execute()
    )
    return result.data[0]


@router.patch("/{task_id}/archive", response_model=TaskResponse)
def archive_task(
    task_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    _get_owned_task(supabase, task_id, current_user.user_id)
    now = datetime.now(timezone.utc).isoformat()
    result = (
        supabase.table("tasks")
        .update({"archived_at": now})
        .eq("id", str(task_id))
        .eq("user_id", current_user.user_id)
        .execute()
    )
    return result.data[0]


@router.patch("/{task_id}/unarchive", response_model=TaskResponse)
def unarchive_task(
    task_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    _get_owned_task(supabase, task_id, current_user.user_id)
    result = (
        supabase.table("tasks")
        .update({"archived_at": None})
        .eq("id", str(task_id))
        .eq("user_id", current_user.user_id)
        .execute()
    )
    return result.data[0]


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: UUID,
    body: DeleteConfirm,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    if not body.confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirmed must be true to permanently delete a task",
        )
    _get_owned_task(supabase, task_id, current_user.user_id)
    supabase.table("tasks").delete().eq("id", str(task_id)).eq(
        "user_id", current_user.user_id
    ).execute()
    return None
