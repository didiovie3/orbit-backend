from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from supabase import Client

from app.core.auth import CurrentUser, get_current_user
from app.db.supabase_client import get_supabase
from app.models.status_update import (
    GenerateStatusUpdateRequest,
    SendStatusUpdateRequest,
    StatusUpdateResponse,
)
from app.services.gemini_service import generate_status_update_draft

router = APIRouter(prefix="/status-updates", tags=["status-updates"])


def _task_ids_for(supabase: Client, status_update_id: str) -> list[str]:
    result = (
        supabase.table("status_update_tasks")
        .select("task_id")
        .eq("status_update_id", status_update_id)
        .execute()
    )
    return [row["task_id"] for row in result.data]


@router.post("/generate", response_model=StatusUpdateResponse, status_code=status.HTTP_201_CREATED)
def generate_draft(
    body: GenerateStatusUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    audience_result = (
        supabase.table("audiences")
        .select("*")
        .eq("id", str(body.audience_id))
        .eq("user_id", current_user.user_id)
        .maybe_single()
        .execute()
    )
    if not audience_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audience not found")
    audience = audience_result.data

    # "Since the last update" means since the last SENT one, not the last
    # generated draft — an abandoned/unsent draft shouldn't reset what
    # counts as new. First-ever update for this audience: no floor at all,
    # pull everything eligible regardless of age.
    last_sent = (
        supabase.table("status_updates")
        .select("sent_at")
        .eq("audience_id", str(body.audience_id))
        .not_.is_("sent_at", "null")
        .order("sent_at", desc=True)
        .limit(1)
        .execute()
    )
    since_timestamp = last_sent.data[0]["sent_at"] if last_sent.data else None

    query = (
        supabase.table("tasks")
        .select("id, label")
        .eq("audience_id", str(body.audience_id))
        .eq("user_id", current_user.user_id)
        .in_("status", ["done", "today", "overdue"])
    )
    if since_timestamp:
        query = query.gt("updated_at", since_timestamp)
    tasks_result = query.execute()

    if not tasks_result.data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No eligible tasks to summarize for this audience since the last update.",
        )

    task_labels = [t["label"] for t in tasks_result.data]
    task_ids = [t["id"] for t in tasks_result.data]

    try:
        draft_text = generate_status_update_draft(
            task_labels=task_labels,
            audience_name=audience["name"],
            tone_notes=audience.get("tone_notes"),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI draft generation failed: {exc}",
        ) from exc

    insert_result = (
        supabase.table("status_updates")
        .insert(
            {
                "user_id": current_user.user_id,
                "audience_id": str(body.audience_id),
                "draft_text": draft_text,
            }
        )
        .execute()
    )
    status_update = insert_result.data[0]

    join_rows = [{"status_update_id": status_update["id"], "task_id": tid} for tid in task_ids]
    supabase.table("status_update_tasks").insert(join_rows).execute()

    return {**status_update, "task_ids": task_ids}


@router.patch("/{status_update_id}/send", response_model=StatusUpdateResponse)
def send_status_update(
    status_update_id: UUID,
    body: SendStatusUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    existing = (
        supabase.table("status_updates")
        .select("*")
        .eq("id", str(status_update_id))
        .eq("user_id", current_user.user_id)
        .maybe_single()
        .execute()
    )
    if not existing.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Status update not found")

    now = datetime.now(timezone.utc).isoformat()
    result = (
        supabase.table("status_updates")
        .update({"sent_text": body.sent_text, "sent_at": now})
        # drive_file_id stays null — "writes a copy to Drive" isn't built
        # yet, same "not built yet" placeholder pattern as every other
        # Drive-touching field elsewhere in this backend.
        .eq("id", str(status_update_id))
        .eq("user_id", current_user.user_id)
        .execute()
    )
    updated = result.data[0]
    task_ids = _task_ids_for(supabase, str(status_update_id))
    return {**updated, "task_ids": task_ids}
