from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from supabase import Client

from app.core.auth import CurrentUser, get_current_user
from app.db.supabase_client import execute_maybe_single, get_supabase
from app.models.common import DeleteConfirm
from app.models.time_block import (
    TimeBlockCreate,
    TimeBlockListResponse,
    TimeBlockResponse,
    TimeBlockUpdate,
)
from app.services.calendar_service import (
    create_calendar_block_event,
    delete_calendar_event,
    update_calendar_block_event,
)
from app.services.calendar_sync import get_access_token
from app.services.projects import is_owned_project

router = APIRouter(prefix="/time-blocks", tags=["time-blocks"])


def _get_owned_time_block(supabase: Client, time_block_id: UUID, user_id: str) -> dict:
    result = execute_maybe_single(
        supabase.table("time_blocks")
        .select("*")
        .eq("id", str(time_block_id))
        .eq("user_id", user_id)
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Time block not found")
    return result.data


@router.get("", response_model=TimeBlockListResponse)
def list_time_blocks(
    project_id: UUID | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    query = supabase.table("time_blocks").select("*").eq("user_id", current_user.user_id)
    if project_id is not None:
        query = query.eq("project_id", str(project_id))

    result = query.order("start_at").execute()
    return {"time_blocks": result.data}


@router.post("", response_model=TimeBlockResponse, status_code=status.HTTP_201_CREATED)
def create_time_block(
    body: TimeBlockCreate,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    if not is_owned_project(supabase, str(body.project_id), current_user.user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # Best-effort push, same spirit as task due_at pushes — a failed or
    # absent calendar connection still leaves a perfectly real time block
    # in Orbit, it just won't show up on Google's side yet.
    calendar_event_id = None
    access_token = get_access_token(supabase, current_user.user_id)
    if access_token:
        try:
            calendar_event_id = create_calendar_block_event(
                access_token,
                body.title,
                body.start_at.isoformat(),
                body.end_at.isoformat(),
                body.note,
            )
        except Exception:
            pass

    insert_result = (
        supabase.table("time_blocks")
        .insert(
            {
                "user_id": current_user.user_id,
                "project_id": str(body.project_id),
                "title": body.title,
                "start_at": body.start_at.isoformat(),
                "end_at": body.end_at.isoformat(),
                "note": body.note,
                "calendar_event_id": calendar_event_id,
            }
        )
        .execute()
    )
    return insert_result.data[0]


@router.patch("/{time_block_id}", response_model=TimeBlockResponse)
def update_time_block(
    time_block_id: UUID,
    body: TimeBlockUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    existing = _get_owned_time_block(supabase, time_block_id, current_user.user_id)

    updates = body.model_dump(exclude_unset=True, exclude={"project_id", "start_at", "end_at"})
    if body.project_id is not None:
        if not is_owned_project(supabase, str(body.project_id), current_user.user_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
        updates["project_id"] = str(body.project_id)
    if body.start_at is not None:
        updates["start_at"] = body.start_at.isoformat()
    if body.end_at is not None:
        updates["end_at"] = body.end_at.isoformat()

    if updates and existing.get("calendar_event_id"):
        access_token = get_access_token(supabase, current_user.user_id)
        if access_token:
            try:
                update_calendar_block_event(
                    access_token,
                    existing["calendar_event_id"],
                    updates.get("title", existing["title"]),
                    updates.get("start_at", existing["start_at"]),
                    updates.get("end_at", existing["end_at"]),
                    updates.get("note", existing.get("note")),
                )
            except Exception:
                pass

    if updates:
        supabase.table("time_blocks").update(updates).eq("id", str(time_block_id)).eq(
            "user_id", current_user.user_id
        ).execute()

    result = execute_maybe_single(supabase.table("time_blocks").select("*").eq("id", str(time_block_id)))
    return result.data


@router.delete("/{time_block_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_time_block(
    time_block_id: UUID,
    body: DeleteConfirm,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    if not body.confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirmed must be true to permanently delete a time block",
        )
    existing = _get_owned_time_block(supabase, time_block_id, current_user.user_id)

    if existing.get("calendar_event_id"):
        access_token = get_access_token(supabase, current_user.user_id)
        if access_token:
            try:
                delete_calendar_event(access_token, existing["calendar_event_id"])
            except Exception:
                pass

    supabase.table("time_blocks").delete().eq("id", str(time_block_id)).eq(
        "user_id", current_user.user_id
    ).execute()
    return None
