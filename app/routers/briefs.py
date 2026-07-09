from fastapi import APIRouter, Depends, HTTPException, Query, status
from supabase import Client

from app.core.auth import CurrentUser, get_current_user
from app.db.supabase_client import get_supabase
from app.models.brief import BriefListResponse, BriefResponse, BriefType, GenerateBriefRequest
from app.services.briefs import (
    generate_eod_brief_content,
    generate_morning_brief_content,
    generate_progress_brief_content,
)

router = APIRouter(prefix="/briefs", tags=["briefs"])

GENERATORS = {
    "morning": generate_morning_brief_content,
    "progress": generate_progress_brief_content,
    "eod": generate_eod_brief_content,
}


@router.get("", response_model=BriefListResponse)
def list_briefs(
    brief_type: BriefType | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    query = supabase.table("daily_briefs").select("*").eq("user_id", current_user.user_id)
    if brief_type is not None:
        query = query.eq("brief_type", brief_type)

    result = query.order("generated_at", desc=True).execute()
    return {"briefs": result.data}


@router.post("/generate", response_model=BriefResponse, status_code=status.HTTP_201_CREATED)
def generate_brief(
    body: GenerateBriefRequest,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    try:
        content = GENERATORS[body.brief_type](supabase, current_user.user_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Brief generation failed: {exc}",
        ) from exc

    insert_result = (
        supabase.table("daily_briefs")
        .insert(
            {
                "user_id": current_user.user_id,
                "brief_type": body.brief_type,
                "content": content,
            }
        )
        .execute()
    )
    return insert_result.data[0]
