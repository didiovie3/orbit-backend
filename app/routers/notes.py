from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from supabase import Client

from app.core.auth import CurrentUser, get_current_user
from app.db.supabase_client import get_supabase
from app.models.common import DeleteConfirm
from app.models.note import (
    NoteCreate,
    NoteListResponse,
    NoteResponse,
    NoteUpdate,
)

router = APIRouter(prefix="/notes", tags=["notes"])


def _get_owned_note(supabase: Client, note_id: UUID, user_id: str) -> dict:
    result = (
        supabase.table("notes")
        .select("*")
        .eq("id", str(note_id))
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    return result.data


@router.get("", response_model=NoteListResponse)
def list_notes(
    project_id: UUID | None = Query(default=None),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    before: datetime | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    query = supabase.table("notes").select("*").eq("user_id", current_user.user_id)

    if project_id is not None:
        query = query.eq("project_id", str(project_id))
    if not include_archived:
        query = query.is_("archived_at", "null")
    if before is not None:
        query = query.lt("updated_at", before.isoformat())

    result = query.order("updated_at", desc=True).limit(limit).execute()
    return {"notes": result.data}


@router.post("", response_model=NoteResponse, status_code=status.HTTP_201_CREATED)
def create_note(
    body: NoteCreate,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    row = {
        "user_id": current_user.user_id,
        "project_id": str(body.project_id) if body.project_id else None,
        "title": body.title,
        "content": body.content,
        # summary and drive_file_id both start null. summary gets filled
        # in by POST /v1/notes/:id/summarise once the Gemini pipeline
        # exists. drive_file_id gets filled in once Drive integration
        # exists — per the contract this normally happens async within
        # ~30 seconds of creation, but that background job isn't built
        # yet either, so it just stays null for now. Neither blocks the
        # note itself from working.
        "summary": None,
        "drive_file_id": None,
    }
    result = supabase.table("notes").insert(row).execute()
    return result.data[0]


@router.get("/{note_id}", response_model=NoteResponse)
def get_note(
    note_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    return _get_owned_note(supabase, note_id, current_user.user_id)


@router.patch("/{note_id}", response_model=NoteResponse)
def update_note(
    note_id: UUID,
    body: NoteUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    _get_owned_note(supabase, note_id, current_user.user_id)

    updates = body.model_dump(exclude_unset=True)
    if "project_id" in updates and updates["project_id"] is not None:
        updates["project_id"] = str(updates["project_id"])
    if not updates:
        return _get_owned_note(supabase, note_id, current_user.user_id)

    result = (
        supabase.table("notes")
        .update(updates)
        .eq("id", str(note_id))
        .eq("user_id", current_user.user_id)
        .execute()
    )
    return result.data[0]


@router.patch("/{note_id}/archive", response_model=NoteResponse)
def archive_note(
    note_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    _get_owned_note(supabase, note_id, current_user.user_id)
    now = datetime.now(timezone.utc).isoformat()
    result = (
        supabase.table("notes")
        .update({"archived_at": now})
        .eq("id", str(note_id))
        .eq("user_id", current_user.user_id)
        .execute()
    )
    return result.data[0]


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    note_id: UUID,
    body: DeleteConfirm,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    if not body.confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirmed must be true to permanently delete a note",
        )
    _get_owned_note(supabase, note_id, current_user.user_id)

    # note_task join rows cascade automatically (ON DELETE CASCADE on
    # note_id, set up back in the schema) — no explicit cleanup needed
    # here, unlike the project->tasks cascade which we handle by hand.
    supabase.table("notes").delete().eq("id", str(note_id)).eq(
        "user_id", current_user.user_id
    ).execute()
    return None
