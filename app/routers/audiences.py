from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from supabase import Client

from app.core.auth import CurrentUser, get_current_user
from app.db.supabase_client import execute_maybe_single, get_supabase
from app.models.audience import (
    AudienceCreate,
    AudienceListResponse,
    AudienceResponse,
    AudienceUpdate,
)
from app.models.common import DeleteConfirm

router = APIRouter(prefix="/audiences", tags=["audiences"])


def _get_owned_audience(supabase: Client, audience_id: UUID, user_id: str) -> dict:
    result = execute_maybe_single(
        supabase.table("audiences")
        .select("*")
        .eq("id", str(audience_id))
        .eq("user_id", user_id)
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audience not found")
    return result.data


@router.get("", response_model=AudienceListResponse)
def list_audiences(
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    result = (
        supabase.table("audiences")
        .select("*")
        .eq("user_id", current_user.user_id)
        .order("created_at")
        .execute()
    )
    return {"audiences": result.data}


@router.post("", response_model=AudienceResponse, status_code=status.HTTP_201_CREATED)
def create_audience(
    body: AudienceCreate,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    row = {
        "user_id": current_user.user_id,
        "name": body.name,
        "tone_notes": body.tone_notes,
    }
    result = supabase.table("audiences").insert(row).execute()
    return result.data[0]


@router.patch("/{audience_id}", response_model=AudienceResponse)
def update_audience(
    audience_id: UUID,
    body: AudienceUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    _get_owned_audience(supabase, audience_id, current_user.user_id)

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return _get_owned_audience(supabase, audience_id, current_user.user_id)

    result = (
        supabase.table("audiences")
        .update(updates)
        .eq("id", str(audience_id))
        .eq("user_id", current_user.user_id)
        .execute()
    )
    return result.data[0]


@router.delete("/{audience_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_audience(
    audience_id: UUID,
    body: DeleteConfirm,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    if not body.confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirmed must be true to delete an audience",
        )
    _get_owned_audience(supabase, audience_id, current_user.user_id)

    # No explicit task cleanup needed here, unlike projects — the
    # audience_id foreign key on tasks is already ON DELETE SET NULL in
    # the schema, so the database correctly orphans affected tasks
    # (setting their audience_id to null) automatically on delete.
    supabase.table("audiences").delete().eq("id", str(audience_id)).eq(
        "user_id", current_user.user_id
    ).execute()
    return None
