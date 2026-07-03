from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from supabase import Client

from app.core.auth import CurrentUser, get_current_user
from app.db.supabase_client import get_supabase
from app.models.common import DeleteConfirm
from app.models.project import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def _get_owned_project(supabase: Client, project_id: UUID, user_id: str) -> dict:
    """Same ownership-check pattern as tasks.py — required since the
    service-role client bypasses Row Level Security."""
    result = (
        supabase.table("projects")
        .select("*")
        .eq("id", str(project_id))
        .eq("owner_id", user_id)
        .maybe_single()
        .execute()
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
        # drive_folder_id stays null here — Drive integration (creating the
        # matching subfolder) isn't built yet. That's a separate future
        # endpoint, not part of Project CRUD itself.
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
    _get_owned_project(supabase, project_id, current_user.user_id)
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
    _get_owned_project(supabase, project_id, current_user.user_id)

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
