from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from supabase import Client

from app.core.auth import CurrentUser, get_current_user
from app.db.supabase_client import execute_maybe_single, get_supabase
from app.models.common import DeleteConfirm
from app.models.project import (
    ProjectCreate,
    ProjectFilesListResponse,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def _get_owned_project(supabase: Client, project_id: UUID, user_id: str) -> dict:
    """Same ownership-check pattern as tasks.py — required since the
    service-role client bypasses Row Level Security."""
    result = execute_maybe_single(
        supabase.table("projects")
        .select("*")
        .eq("id", str(project_id))
        .eq("owner_id", user_id)
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return result.data


@router.get("", response_model=ProjectListResponse)
def list_projects(
    include_archived: bool = Query(default=False),
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    query = supabase.table("projects").select("*").eq("owner_id", current_user.user_id)
    if not include_archived:
        query = query.is_("archived_at", "null")
    result = query.order("created_at").execute()
    return {"projects": result.data}


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectCreate,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    row = {
        "owner_id": current_user.user_id,
        "name": body.name,
        "color": body.color,
        "map_x": body.map_x,
        "map_y": body.map_y,
        "is_unsorted": False,  # only the signup trigger creates the real Unsorted project
        # drive_folder_id stays null here — created lazily (and cached) the
        # first time this project actually writes something to Drive, via
        # get_or_create_project_drive_folder, not eagerly at creation time.
    }
    result = supabase.table("projects").insert(row).execute()
    return result.data[0]


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: UUID,
    body: ProjectUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    _get_owned_project(supabase, project_id, current_user.user_id)

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return _get_owned_project(supabase, project_id, current_user.user_id)

    result = (
        supabase.table("projects")
        .update(updates)
        .eq("id", str(project_id))
        .eq("owner_id", current_user.user_id)
        .execute()
    )
    return result.data[0]


@router.patch("/{project_id}/archive", response_model=ProjectResponse)
def archive_project(
    project_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    project = _get_owned_project(supabase, project_id, current_user.user_id)
    if project["is_unsorted"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The Unsorted project can't be archived",
        )
    now = datetime.now(timezone.utc).isoformat()

    # Cascade: archive every currently-active task under this project too.
    # See the note above create_task in tasks.py-adjacent docs — this is
    # the simpler interpretation (sweeps up everything currently active,
    # doesn't try to distinguish already-archived tasks from these).
    supabase.table("tasks").update({"archived_at": now}).eq(
        "project_id", str(project_id)
    ).eq("user_id", current_user.user_id).is_("archived_at", "null").execute()

    result = (
        supabase.table("projects")
        .update({"archived_at": now})
        .eq("id", str(project_id))
        .eq("owner_id", current_user.user_id)
        .execute()
    )
    return result.data[0]


@router.patch("/{project_id}/unarchive", response_model=ProjectResponse)
def unarchive_project(
    project_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    _get_owned_project(supabase, project_id, current_user.user_id)

    # Restore every currently-archived task under this project.
    supabase.table("tasks").update({"archived_at": None}).eq(
        "project_id", str(project_id)
    ).eq("user_id", current_user.user_id).execute()

    result = (
        supabase.table("projects")
        .update({"archived_at": None})
        .eq("id", str(project_id))
        .eq("owner_id", current_user.user_id)
        .execute()
    )
    return result.data[0]


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: UUID,
    body: DeleteConfirm,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    if not body.confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirmed must be true to permanently delete a project",
        )
    project = _get_owned_project(supabase, project_id, current_user.user_id)
    if project["is_unsorted"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The Unsorted project can't be deleted",
        )

    # Explicit cascade, not a DB-level one — see the note above this file's
    # import block. Deleting each task this way still lets THEIR own
    # foreign keys (reminders, note links, etc.) cascade correctly.
    supabase.table("tasks").delete().eq("project_id", str(project_id)).eq(
        "user_id", current_user.user_id
    ).execute()

    supabase.table("projects").delete().eq("id", str(project_id)).eq(
        "owner_id", current_user.user_id
    ).execute()
    return None


@router.get("/{project_id}/files", response_model=ProjectFilesListResponse)
def get_project_files(
    project_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """
    Every real Drive-backed file belonging to this project: task
    attachments plus notes, whichever actually synced (drive_file_id set —
    excludes anything created while Drive was disconnected, since there's
    nothing in Drive to link back to for those). Two queries rather than
    an embedded join, matching this backend's usual style elsewhere.
    """
    _get_owned_project(supabase, project_id, current_user.user_id)

    task_ids_result = (
        supabase.table("tasks")
        .select("id")
        .eq("project_id", str(project_id))
        .eq("user_id", current_user.user_id)
        .execute()
    )
    task_ids = [t["id"] for t in task_ids_result.data]

    attachments: list[dict] = []
    if task_ids:
        attachments_result = (
            supabase.table("task_attachments")
            .select("id, file_name, drive_file_id, created_at")
            .in_("task_id", task_ids)
            .not_.is_("drive_file_id", "null")
            .execute()
        )
        attachments = [
            {
                "id": a["id"],
                "name": a["file_name"],
                "drive_file_id": a["drive_file_id"],
                "created_at": a["created_at"],
                "source": "attachment",
            }
            for a in attachments_result.data
        ]

    notes_result = (
        supabase.table("notes")
        .select("id, title, drive_file_id, created_at")
        .eq("project_id", str(project_id))
        .eq("user_id", current_user.user_id)
        .not_.is_("drive_file_id", "null")
        .execute()
    )
    notes = [
        {
            "id": n["id"],
            "name": n["title"] or "Untitled note",
            "drive_file_id": n["drive_file_id"],
            "created_at": n["created_at"],
            "source": "note",
        }
        for n in notes_result.data
    ]

    files = sorted(attachments + notes, key=lambda f: f["created_at"], reverse=True)
    return {"files": files}
