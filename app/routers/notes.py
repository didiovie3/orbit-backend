from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from supabase import Client

from app.core.auth import CurrentUser, get_current_user
from app.db.supabase_client import execute_maybe_single, get_supabase
from app.models.common import DeleteConfirm
from app.models.note import (
    NoteCreate,
    NoteListResponse,
    NoteResponse,
    NoteUpdate,
)
from app.services.drive_service import (
    get_or_create_project_drive_folder,
    update_file_content,
    upload_file,
)
from app.services.drive_service import get_access_token as get_drive_access_token
from app.services.gemini_service import generate_note_summary
from app.services.projects import get_unsorted_project_id

router = APIRouter(prefix="/notes", tags=["notes"])


def _get_owned_note(supabase: Client, note_id: UUID, user_id: str) -> dict:
    result = execute_maybe_single(
        supabase.table("notes")
        .select("*")
        .eq("id", str(note_id))
        .eq("user_id", user_id)
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    return result.data


def _sync_note_to_drive(
    supabase: Client,
    user_id: str,
    project_id: str,
    title: str | None,
    content: str,
    existing_drive_file_id: str | None,
) -> str | None:
    """
    Best-effort — a note is still perfectly real in Orbit even if Drive
    isn't connected or this fails partway, same degrade-gracefully pattern
    as every other Drive-touching write in this backend (see
    app/routers/tasks.py's attachment upload). Keeps the same Drive file's
    content current on every edit rather than accumulating a new file per
    save. A brand new file lands in the note's own project's Drive
    subfolder (so the Project Detail Files tab can list it back) — moving
    projects later doesn't relocate an already-uploaded file, same as
    Drive files never following a task/attachment that gets reassigned.
    Returns the drive_file_id to store — unchanged, newly created, or
    still None if Drive isn't connected.
    """
    access_token = get_drive_access_token(supabase, user_id)
    if not access_token:
        return existing_drive_file_id

    try:
        if existing_drive_file_id:
            update_file_content(access_token, existing_drive_file_id, "text/plain", content.encode("utf-8"))
            return existing_drive_file_id

        user_result = execute_maybe_single(supabase.table("users").select("drive_root_folder_id").eq("id", user_id))
        root_folder_id = user_result.data.get("drive_root_folder_id") if user_result.data else None
        project_result = execute_maybe_single(supabase.table("projects").select("id, name").eq("id", project_id))
        project = project_result.data
        if not root_folder_id or not project:
            return existing_drive_file_id

        folder_id = get_or_create_project_drive_folder(supabase, access_token, root_folder_id, project["id"], project["name"])
        file_name = title or "Untitled note"
        return upload_file(access_token, folder_id, file_name, "text/plain", content.encode("utf-8"))
    except Exception:
        return existing_drive_file_id


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
    # "No project" means the real Unsorted project, never a null FK — same
    # invariant as tasks.py's create_task (see app/services/projects.py).
    project_id = str(body.project_id) if body.project_id else get_unsorted_project_id(supabase, current_user.user_id)

    row = {
        "user_id": current_user.user_id,
        "project_id": project_id,
        "title": body.title,
        "content": body.content,
        # summary gets filled in by POST /v1/notes/:id/summarise, not here.
        "summary": None,
        "drive_file_id": None,
    }
    result = supabase.table("notes").insert(row).execute()
    note = result.data[0]

    drive_file_id = _sync_note_to_drive(supabase, current_user.user_id, project_id, body.title, body.content, None)
    if drive_file_id:
        update_result = (
            supabase.table("notes").update({"drive_file_id": drive_file_id}).eq("id", note["id"]).execute()
        )
        note = update_result.data[0]

    return note


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
    existing_note = _get_owned_note(supabase, note_id, current_user.user_id)

    updates = body.model_dump(exclude_unset=True)
    if "project_id" in updates:
        if updates["project_id"] is None:
            # Explicit unassign maps to the real Unsorted project, not a
            # null FK — same invariant as create_note.
            updates["project_id"] = get_unsorted_project_id(supabase, current_user.user_id)
        else:
            updates["project_id"] = str(updates["project_id"])
    if not updates:
        return existing_note

    if "content" in updates:
        drive_file_id = _sync_note_to_drive(
            supabase,
            current_user.user_id,
            updates.get("project_id", existing_note.get("project_id")),
            updates.get("title", existing_note.get("title")),
            updates["content"],
            existing_note.get("drive_file_id"),
        )
        if drive_file_id != existing_note.get("drive_file_id"):
            updates["drive_file_id"] = drive_file_id

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


@router.patch("/{note_id}/unarchive", response_model=NoteResponse)
def unarchive_note(
    note_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    _get_owned_note(supabase, note_id, current_user.user_id)
    result = (
        supabase.table("notes")
        .update({"archived_at": None})
        .eq("id", str(note_id))
        .eq("user_id", current_user.user_id)
        .execute()
    )
    return result.data[0]


@router.post("/{note_id}/summarise", response_model=NoteResponse)
def summarise_note(
    note_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    note = _get_owned_note(supabase, note_id, current_user.user_id)

    if not note["content"].strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Note has no content to summarise",
        )

    user_result = execute_maybe_single(
        supabase.table("users")
        .select("writing_style_notes")
        .eq("id", current_user.user_id)
    )
    writing_style_notes = user_result.data.get("writing_style_notes") if user_result.data else None

    try:
        summary = generate_note_summary(note["content"], writing_style_notes=writing_style_notes)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Summary generation failed: {exc}",
        ) from exc

    result = (
        supabase.table("notes")
        .update({"summary": summary})
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
