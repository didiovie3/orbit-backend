import json

from fastapi import APIRouter, Depends, HTTPException, status
from supabase import Client

from app.core.auth import CurrentUser, get_current_user
from app.db.supabase_client import execute_maybe_single, get_supabase
from app.models.drive import ConnectDriveRequest, ConnectDriveResponse
from app.services.calendar_service import exchange_auth_code, revoke_token
from app.services.drive_service import find_or_create_orbit_folder

router = APIRouter(prefix="/drive", tags=["drive"])


@router.post("/connect", response_model=ConnectDriveResponse, status_code=status.HTTP_201_CREATED)
def connect_drive(
    body: ConnectDriveRequest,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    try:
        tokens = exchange_auth_code(body.auth_code)
        root_folder_id = find_or_create_orbit_folder(tokens["access_token"])
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Google Drive connect failed: {exc}",
        ) from exc

    stored_token_json = json.dumps(
        {
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "expires_at": tokens["expires_at"],
        }
    )
    supabase.table("users").update(
        {
            "google_drive_token": stored_token_json,
            "drive_root_folder_id": root_folder_id,
        }
    ).eq("id", current_user.user_id).execute()

    return {"connected": True, "root_folder_id": root_folder_id}


@router.delete("/disconnect", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_drive(
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    user_result = execute_maybe_single(
        supabase.table("users").select("google_drive_token").eq("id", current_user.user_id)
    )
    stored = user_result.data.get("google_drive_token") if user_result.data else None
    if stored:
        token_data = json.loads(stored)
        revoke_token(token_data.get("refresh_token") or token_data.get("access_token"))

    supabase.table("users").update(
        {"google_drive_token": None, "drive_root_folder_id": None}
    ).eq("id", current_user.user_id).execute()
    return None
