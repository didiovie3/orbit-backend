from supabase import Client

import httpx

# Auth code exchange, token revocation, and the stored-token refresh helper
# are identical to Calendar's — same Google OAuth token endpoints, same
# client_id/secret, nothing scope-specific about any of them. Reused
# directly rather than duplicated.
from app.services.calendar_service import exchange_auth_code, get_valid_access_token, revoke_token  # noqa: F401

DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"


def get_access_token(supabase: Client, user_id: str) -> str | None:
    """Same shape as calendar_sync.get_access_token, just reading the
    google_drive_token column instead — the two connections are
    independent, so a user can have one without the other."""
    user_result = (
        supabase.table("users").select("google_drive_token").eq("id", user_id).maybe_single().execute()
    )
    stored = user_result.data.get("google_drive_token") if user_result.data else None
    if not stored:
        return None

    access_token, updated_json = get_valid_access_token(stored)
    if updated_json:
        supabase.table("users").update({"google_drive_token": updated_json}).eq("id", user_id).execute()
    return access_token


def find_or_create_orbit_folder(access_token: str) -> str:
    """
    Drive is write-only in v1 (see DriveApi.kt on the client) — everything
    Orbit writes goes in a single top-level "Orbit" folder. Looks for an
    existing one first so reconnecting after a disconnect reuses the same
    folder instead of creating a duplicate every time.
    """
    headers = {"Authorization": f"Bearer {access_token}"}

    search = httpx.get(
        DRIVE_FILES_URL,
        headers=headers,
        params={
            "q": "name='Orbit' and mimeType='application/vnd.google-apps.folder' and trashed=false",
            "fields": "files(id)",
            "spaces": "drive",
        },
    )
    search.raise_for_status()
    existing = search.json().get("files", [])
    if existing:
        return existing[0]["id"]

    create = httpx.post(
        DRIVE_FILES_URL,
        headers=headers,
        json={"name": "Orbit", "mimeType": "application/vnd.google-apps.folder"},
    )
    create.raise_for_status()
    return create.json()["id"]


def upload_file(access_token: str, folder_id: str, file_name: str, mime_type: str, content: bytes) -> str:
    """
    Two requests rather than one true multipart/related upload — Drive's
    "simple" single-request upload needs a hand-built multipart/related
    body, which isn't worth the complexity here: create the file's
    metadata first (name + parent folder), then PATCH the actual bytes
    into it. One extra round-trip, much simpler to get right.
    """
    headers = {"Authorization": f"Bearer {access_token}"}

    create = httpx.post(
        DRIVE_FILES_URL,
        headers=headers,
        json={"name": file_name, "parents": [folder_id]},
    )
    create.raise_for_status()
    file_id = create.json()["id"]

    upload = httpx.patch(
        f"{DRIVE_UPLOAD_URL}/{file_id}",
        params={"uploadType": "media"},
        headers={**headers, "Content-Type": mime_type},
        content=content,
    )
    upload.raise_for_status()
    return file_id
