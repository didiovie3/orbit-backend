from fastapi import APIRouter, Depends, HTTPException, status
from supabase import Client

from app.core.auth import CurrentUser, get_current_user
from app.db.supabase_client import get_supabase
from app.models.user import UserResponse, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


def _to_response(row: dict) -> dict:
    return {
        **row,
        "calendar_connected": row.get("google_calendar_token") is not None,
        "drive_connected": row.get("google_drive_token") is not None,
    }


@router.get("/me", response_model=UserResponse)
def get_me(
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    result = (
        supabase.table("users").select("*").eq("id", current_user.user_id).maybe_single().execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return _to_response(result.data)


@router.patch("/me", response_model=UserResponse)
def update_me(
    body: UserUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    updates = body.model_dump(exclude_unset=True)
    if updates:
        supabase.table("users").update(updates).eq("id", current_user.user_id).execute()

    result = (
        supabase.table("users").select("*").eq("id", current_user.user_id).maybe_single().execute()
    )
    return _to_response(result.data)
