from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from supabase import Client

from app.db.supabase_client import get_supabase

router = APIRouter(prefix="/auth", tags=["auth"])


class EmailCheckResponse(BaseModel):
    exists: bool
    providers: list[str] = []


@router.get("/check-email", response_model=EmailCheckResponse)
def check_email(
    email: str = Query(..., min_length=3),
    supabase: Client = Depends(get_supabase),
):
    """
    Public, unauthenticated on purpose — this runs from the login/signup
    screen before any session exists. Used to keep a single email tied to
    one sign-in method (LoginScreen.kt blocks email/password signup against
    a Google-only email and vice versa) instead of relying on whatever
    Supabase's own cross-provider linking would otherwise do.

    list_users() is the only lookup the admin API offers for this (no
    get-user-by-email) — fine at this user count; would need real
    pagination/search if the user base grows significantly.
    """
    normalized = email.strip().lower()
    users = supabase.auth.admin.list_users()
    match = next((u for u in users if (u.email or "").lower() == normalized), None)
    if not match:
        return {"exists": False, "providers": []}
    providers = (match.app_metadata or {}).get("providers", [])
    return {"exists": True, "providers": providers}
