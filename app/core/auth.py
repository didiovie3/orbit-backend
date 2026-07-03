from functools import lru_cache

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from jwt import PyJWTError, PyJWKClientError

from app.core.config import Settings, get_settings

bearer_scheme = HTTPBearer()


@lru_cache
def get_jwks_client(supabase_url: str) -> jwt.PyJWKClient:
    """
    Newer Supabase projects sign tokens with an asymmetric algorithm
    (ES256) instead of a shared secret. Verifying those means fetching
    Supabase's public key from this well-known URL rather than using
    anything from our own .env. PyJWKClient handles the fetching and
    caching of those public keys internally — this lru_cache just makes
    sure we build one client per Supabase URL, not one per request.
    """
    jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
    return jwt.PyJWKClient(jwks_url, cache_keys=True)


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
    Verifies the Authorization: Bearer <token> header. Every /v1 route
    that touches user data should depend on this — it's what makes
    `user_id = current_user.user_id` trustworthy instead of something
    the client could spoof.

    Supabase projects can sign tokens one of two ways depending on when
    the project was created / whether it's been migrated:
      - ES256 (asymmetric, newer default) — verified against Supabase's
        published public key, fetched from a JWKS endpoint.
      - HS256 (symmetric, legacy) — verified against SUPABASE_JWT_SECRET
        from .env, the shared-secret approach this originally assumed.
    We check the token's own header to see which one it's actually
    using and verify accordingly, rather than assuming.

    Raises 401 on any failure — expired, malformed, wrong signature, or
    missing 'sub' claim.
    """
    token = credentials.credentials

    try:
        unverified_header = jwt.get_unverified_header(token)
        algorithm = unverified_header.get("alg", "HS256")

        if algorithm == "ES256":
            jwks_client = get_jwks_client(settings.supabase_url)
            signing_key = jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["ES256"],
                audience="authenticated",
            )
        else:
            payload = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
            )
    except (PyJWTError, PyJWKClientError) as exc:
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
