from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from jwt import PyJWTError

from app.core.config import Settings, get_settings

bearer_scheme = HTTPBearer()


class CurrentUser:
    """Minimal user context extracted from a verified Supabase JWT."""

    def __init__(self, user_id: str, email: str | None):
        self.user_id = user_id
        self.email = email


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    """
    Verifies the Authorization: Bearer <token> header against Supabase's
    JWT secret. Every /v1 route that touches user data should depend on
    this — it's what makes `user_id = current_user.user_id` trustworthy
    instead of something the client could spoof.

    Raises 401 on any failure — expired, malformed, wrong signature, or
    missing 'sub' claim.
    """
    token = credentials.credentials
    try:
        payload = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim",
        )

    return CurrentUser(user_id=user_id, email=payload.get("email"))
