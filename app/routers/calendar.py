import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from supabase import Client

from app.core.auth import CurrentUser, get_current_user
from app.db.supabase_client import get_supabase
from app.models.calendar import ConnectCalendarRequest, ConnectCalendarResponse, SyncResponse
from app.services.calendar_service import exchange_auth_code, revoke_token
from app.services.calendar_sync import run_two_way_sync

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.post("/connect", response_model=ConnectCalendarResponse, status_code=status.HTTP_201_CREATED)
def connect_calendar(
    body: ConnectCalendarRequest,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    try:
        tokens = exchange_auth_code(body.auth_code)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Google token exchange failed: {exc}",
        ) from exc

    # channel_token: a secret we'd hand to Google's events.watch() call so
    # it echoes it back in every webhook request's X-Goog-Channel-Token
    # header, letting us verify a request genuinely came from a channel
    # we registered. Generated and stored now so the design is complete
    # and testable — but see calendar_sync.py's module docstring: the
    # actual watch() registration call isn't wired up yet, since it needs
    # a public webhook URL this local setup doesn't have.
    stored_token_json = json.dumps(
        {
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "expires_at": tokens["expires_at"],
            "channel_token": secrets.token_urlsafe(32),
        }
    )
    supabase.table("users").update({"google_calendar_token": stored_token_json}).eq(
        "id", current_user.user_id
    ).execute()

    return {"connected": True, "calendar_email": tokens["email"]}


@router.post("/sync", response_model=SyncResponse)
def manual_sync(
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    result = run_two_way_sync(supabase, current_user.user_id)
    if not result["connected"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Calendar isn't connected — call /v1/calendar/connect first.",
        )
    return result


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def calendar_webhook(request: Request, supabase: Client = Depends(get_supabase)):
    """
    Called by Google, not by the Android app — there's no auth header to
    check here the normal way (Google isn't a logged-in Orbit user), which
    is exactly why verifying X-Goog-Channel-Token matters: it's the only
    thing standing between this endpoint and anyone who guesses the URL.

    Google's push notifications only ever say "something changed, go
    check" — they never include the actual changed data in the request
    itself. So the real work here is just: verify the token, figure out
    which user's channel this is, and re-run the same sync logic the
    manual endpoint uses.
    """
    channel_token = request.headers.get("X-Goog-Channel-Token")
    if not channel_token:
        return {}  # Google ignores the response body regardless; nothing to do without a token

    users_result = supabase.table("users").select("id, google_calendar_token").execute()
    matching_user_id = None
    for user in users_result.data:
        stored = user.get("google_calendar_token")
        if not stored:
            continue
        if json.loads(stored).get("channel_token") == channel_token:
            matching_user_id = user["id"]
            break

    if matching_user_id:
        run_two_way_sync(supabase, matching_user_id)

    return {}


@router.delete("/disconnect", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_calendar(
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    user_result = (
        supabase.table("users").select("google_calendar_token").eq("id", current_user.user_id).maybe_single().execute()
    )
    stored = user_result.data.get("google_calendar_token") if user_result.data else None
    if stored:
        token_data = json.loads(stored)
        revoke_token(token_data.get("refresh_token") or token_data.get("access_token"))

    supabase.table("users").update({"google_calendar_token": None}).eq("id", current_user.user_id).execute()
    return None
