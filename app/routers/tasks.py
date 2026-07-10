from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from supabase import Client

from app.core.auth import CurrentUser, get_current_user
from app.db.supabase_client import execute_maybe_single, get_supabase
from app.models.attachment import AttachmentListResponse, AttachmentResponse
from app.models.common import DeleteConfirm
from app.models.task import (
    TaskCreate,
    TaskListResponse,
    TaskResponse,
    TaskUpdate,
    SetRemindersRequest,
    RemindersListResponse,
    SubtaskReorderRequest,
)
from app.services.audiences import is_owned_audience
from app.services.drive_service import get_access_token, get_or_create_project_drive_folder, upload_file
from app.services.projects import get_unsorted_project_id, is_owned_project

router = APIRouter(prefix="/tasks", tags=["tasks"])

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # 20MB — same order of magnitude as capture's audio/image limits


def _get_owned_task(supabase: Client, task_id: UUID, user_id: str) -> dict:
    """
    Shared by every endpoint below that operates on a single task. Since
    we're using the service-role Supabase client (bypasses Row Level
    Security by design — see supabase_client.py), WE are responsible for
    checking the task actually belongs to the requesting user, not the
    database. Forgetting this check on any one endpoint would let any
    logged-in user read or modify anyone else's tasks by guessing IDs.
    """
    result = execute_maybe_single(
        supabase.table("tasks")
        .select("*")
        .eq("id", str(task_id))
        .eq("user_id", user_id)
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return result.data


@router.get("", response_model=TaskListResponse)
def list_tasks(
    project_id: UUID | None = Query(default=None),
    parent_task_id: UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    include_archived: bool = Query(default=False),
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    query = supabase.table("tasks").select("*").eq("user_id", current_user.user_id)

    if project_id is not None:
        query = query.eq("project_id", str(project_id))
    if parent_task_id is not None:
        query = query.eq("parent_task_id", str(parent_task_id))
    if status_filter is not None:
        query = query.eq("status", status_filter)
    if not include_archived:
        query = query.is_("archived_at", "null")

    # Sub-tasks have a deliberate manual order (drag-reordered in the app);
    # top-level tasks don't, so they keep sorting by urgency as always.
    if parent_task_id is not None:
        result = query.order("sort_index").execute()
    else:
        result = query.order("urgency", desc=True).execute()
    return {"tasks": result.data}


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    body: TaskCreate,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    if body.project_id and not is_owned_project(supabase, str(body.project_id), current_user.user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # "No project" means the real Unsorted project, never a null FK — see
    # app/services/projects.py. Sub-tasks inherit sort_index scoping by
    # parent_task_id regardless, so this only affects project_id itself.
    project_id = str(body.project_id) if body.project_id else get_unsorted_project_id(supabase, current_user.user_id)

    if body.audience_id and not is_owned_audience(supabase, str(body.audience_id), current_user.user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audience not found")

    if body.parent_task_id:
        # Raises 404 if the parent doesn't exist or isn't this user's —
        # otherwise a client could nest its own new task under someone
        # else's, same ownership gap as project_id/audience_id above.
        _get_owned_task(supabase, body.parent_task_id, current_user.user_id)

    sort_index = 0
    if body.parent_task_id:
        # Append after existing siblings — new sub-tasks land at the end of
        # the list rather than colliding with (or jumping ahead of) whatever
        # order's already been drag-reordered.
        siblings = (
            supabase.table("tasks")
            .select("id", count="exact")
            .eq("parent_task_id", str(body.parent_task_id))
            .eq("user_id", current_user.user_id)
            .execute()
        )
        sort_index = siblings.count or 0

    row = {
        "user_id": current_user.user_id,
        "project_id": project_id,
        "parent_task_id": str(body.parent_task_id) if body.parent_task_id else None,
        "audience_id": str(body.audience_id) if body.audience_id else None,
        "label": body.label,
        "location": body.location,
        "base_urgency": body.base_urgency,
        "urgency": body.base_urgency,  # starts equal to base_urgency; escalation job raises it later
        "escalation_enabled": body.escalation_enabled,
        "due_at": body.due_at.isoformat() if body.due_at else None,
        "source": body.source,
        "sort_index": sort_index,
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


@router.get("/{task_id}/reminders", response_model=RemindersListResponse)
def get_task_reminders(
    task_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    _get_owned_task(supabase, task_id, current_user.user_id)
    result = (
        supabase.table("task_reminder_preferences")
        .select("*")
        .eq("task_id", str(task_id))
        .execute()
    )
    return {"reminders": result.data}


@router.post("/{task_id}/reminders", response_model=RemindersListResponse)
def set_task_reminders(
    task_id: UUID,
    body: SetRemindersRequest,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    _get_owned_task(supabase, task_id, current_user.user_id)
    
    # Delete old reminders
    supabase.table("task_reminder_preferences").delete().eq("task_id", str(task_id)).execute()
    
    # Insert new reminders
    if body.remind_at_list:
        rows = [{"task_id": str(task_id), "remind_at": dt.isoformat()} for dt in body.remind_at_list]
        result = supabase.table("task_reminder_preferences").insert(rows).execute()
        return {"reminders": result.data}
    return {"reminders": []}


@router.get("/{task_id}/attachments", response_model=AttachmentListResponse)
def get_task_attachments(
    task_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    _get_owned_task(supabase, task_id, current_user.user_id)
    result = (
        supabase.table("task_attachments")
        .select("*")
        .eq("task_id", str(task_id))
        .order("created_at")
        .execute()
    )
    return {"attachments": result.data}


@router.post("/{task_id}/attachments", response_model=AttachmentResponse, status_code=status.HTTP_201_CREATED)
async def upload_task_attachment(
    task_id: UUID,
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    task = _get_owned_task(supabase, task_id, current_user.user_id)

    content = await file.read()
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Attachment too large (max 20MB)",
        )

    # Best-effort push to Drive, same spirit as every other Drive write in
    # this backend — a task attachment is still perfectly real in Orbit
    # even if Drive isn't connected or the upload fails partway. Lands in
    # the task's own project's Drive subfolder (not the flat root) so the
    # Project Detail Files tab can list it back.
    drive_file_id = None
    access_token = get_access_token(supabase, current_user.user_id)
    if access_token:
        user_result = execute_maybe_single(
            supabase.table("users").select("drive_root_folder_id").eq("id", current_user.user_id)
        )
        root_folder_id = user_result.data.get("drive_root_folder_id") if user_result.data else None
        project_result = execute_maybe_single(
            supabase.table("projects").select("id, name").eq("id", task["project_id"])
        )
        project = project_result.data
        if root_folder_id and project:
            try:
                project_folder_id = get_or_create_project_drive_folder(
                    supabase, access_token, root_folder_id, project["id"], project["name"]
                )
                drive_file_id = upload_file(
                    access_token,
                    project_folder_id,
                    file.filename or "attachment",
                    file.content_type or "application/octet-stream",
                    content,
                )
            except Exception:
                pass

    insert_result = (
        supabase.table("task_attachments")
        .insert(
            {
                "task_id": str(task_id),
                "user_id": current_user.user_id,
                "file_name": file.filename or "attachment",
                "mime_type": file.content_type or "application/octet-stream",
                "size_bytes": len(content),
                "drive_file_id": drive_file_id,
            }
        )
        .execute()
    )
    return insert_result.data[0]


@router.delete("/{task_id}/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task_attachment(
    task_id: UUID,
    attachment_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    _get_owned_task(supabase, task_id, current_user.user_id)
    # Only removes Orbit's own reference — per the established Drive policy
    # (see Settings' privacy copy), files already written there are never
    # deleted from Drive itself, even when unlinked in Orbit.
    supabase.table("task_attachments").delete().eq("id", str(attachment_id)).eq(
        "task_id", str(task_id)
    ).execute()
    return None


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
    # An explicit {"project_id": null} means "unassign", which maps to the
    # real Unsorted project rather than a null FK — same invariant as
    # create_task (see app/services/projects.py).
    if "project_id" in updates and updates["project_id"] is None:
        updates["project_id"] = get_unsorted_project_id(supabase, current_user.user_id)
    for uuid_field in ("project_id", "parent_task_id", "audience_id"):
        if uuid_field in updates and updates[uuid_field] is not None:
            updates[uuid_field] = str(updates[uuid_field])

    if updates.get("audience_id") and not is_owned_audience(supabase, updates["audience_id"], current_user.user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audience not found")

    if updates.get("project_id") and not is_owned_project(supabase, updates["project_id"], current_user.user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    if updates.get("parent_task_id"):
        # Raises 404 if not this user's — see the same check in create_task.
        _get_owned_task(supabase, updates["parent_task_id"], current_user.user_id)

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


@router.patch("/{task_id}/subtasks/reorder", status_code=status.HTTP_204_NO_CONTENT)
def reorder_subtasks(
    task_id: UUID,
    body: SubtaskReorderRequest,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    _get_owned_task(supabase, task_id, current_user.user_id)  # 404s if parent isn't owned

    # Validate every id up front — updating sort_index one at a time and
    # only discovering a bad id partway through would leave the list
    # half-reordered with no way back, since earlier updates in the loop
    # already committed.
    existing = (
        supabase.table("tasks")
        .select("id")
        .eq("user_id", current_user.user_id)
        .eq("parent_task_id", str(task_id))
        .execute()
    )
    existing_ids = {row["id"] for row in existing.data}
    missing = [str(sub_id) for sub_id in body.task_ids if str(sub_id) not in existing_ids]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sub-task {missing[0]} not found under this parent",
        )

    for idx, sub_id in enumerate(body.task_ids):
        supabase.table("tasks").update({"sort_index": idx}).eq("id", str(sub_id)).eq(
            "user_id", current_user.user_id
        ).eq("parent_task_id", str(task_id)).execute()
    return None


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
