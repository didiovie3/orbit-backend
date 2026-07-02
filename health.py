from fastapi import APIRouter, Depends

from app.core.auth import CurrentUser, get_current_user

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    """No auth required — just confirms the server is up and reachable."""
    return {"status": "ok"}


@router.get("/whoami")
def whoami(current_user: CurrentUser = Depends(get_current_user)):
    """
    Auth-gated. Once this returns your Supabase user id from a real
    Android-issued token, the JWT dependency is proven end-to-end —
    everything else in Step 5 builds on this working.
    """
    return {"user_id": current_user.user_id, "email": current_user.email}
