import json
from datetime import datetime, timedelta, timezone

import httpx

from app.core.config import get_settings

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


def exchange_auth_code(auth_code: str) -> dict:
    """
    Trades a one-time auth_code (from the Android Google Sign-In consent
    screen) for a real access_token + refresh_token. The access_token is
    short-lived (~1 hour); the refresh_token is what makes this durable —
    it's what refresh_access_token() below uses to get new access_tokens
    indefinitely, without the user re-consenting.
    """
    settings = get_settings()
    response = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": auth_code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    response.raise_for_status()
    token_data = response.json()

    userinfo = httpx.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {token_data['access_token']}"},
    )
    userinfo.raise_for_status()
    email = userinfo.json().get("email")

    return {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token"),
        "expires_at": (
            datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])
        ).isoformat(),
        "email": email,
    }


def refresh_access_token(refresh_token: str) -> dict:
    """Gets a fresh access_token using the durable refresh_token — needed
    every time the stored access_token has expired (roughly hourly)."""
    settings = get_settings()
    response = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "refresh_token": refresh_token,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "grant_type": "refresh_token",
        },
    )
    response.raise_for_status()
    token_data = response.json()
    return {
        "access_token": token_data["access_token"],
        "expires_at": (
            datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])
        ).isoformat(),
    }


def revoke_token(token: str) -> None:
    """Best-effort — if this fails, we still clear our own stored copy;
    Google's side eventually expires unused tokens regardless."""
    try:
        httpx.post(GOOGLE_REVOKE_URL, data={"token": token})
    except httpx.HTTPError:
        pass


def get_valid_access_token(stored_token_json: str) -> tuple[str, str | None]:
    """
    Returns (access_token, updated_token_json). The second value is None
    if the stored token didn't need refreshing, or the new JSON to save
    back to the database if it did — the caller is responsible for
    persisting that, since this function doesn't touch the database
    itself (keeps this module's job purely "talk to Google").
    """
    stored = json.loads(stored_token_json)
    expires_at = datetime.fromisoformat(stored["expires_at"])

    if datetime.now(timezone.utc) < expires_at - timedelta(minutes=5):
        return stored["access_token"], None

    refreshed = refresh_access_token(stored["refresh_token"])
    updated = {**stored, **refreshed}
    return updated["access_token"], json.dumps(updated)


def list_calendar_events(access_token: str, updated_min: str | None = None) -> list[dict]:
    params = {"singleEvents": "true", "orderBy": "updated"}
    if updated_min:
        params["updatedMin"] = updated_min
    response = httpx.get(
        CALENDAR_API_BASE,
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
    )
    response.raise_for_status()
    return response.json().get("items", [])


def create_calendar_event(access_token: str, label: str, due_at: str) -> str:
    """Returns the new event's id, to be stored as the task's calendar_event_id."""
    response = httpx.post(
        CALENDAR_API_BASE,
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "summary": label,
            "start": {"dateTime": due_at},
            "end": {"dateTime": due_at},  # tasks are point-in-time deadlines, not spans
        },
    )
    response.raise_for_status()
    return response.json()["id"]


def update_calendar_event(access_token: str, event_id: str, label: str, due_at: str) -> None:
    response = httpx.patch(
        f"{CALENDAR_API_BASE}/{event_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "summary": label,
            "start": {"dateTime": due_at},
            "end": {"dateTime": due_at},
        },
    )
    response.raise_for_status()
