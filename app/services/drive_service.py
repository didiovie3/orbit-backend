from supabase import Client

import httpx

from app.db.supabase_client import execute_maybe_single

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
    user_result = execute_maybe_single(
        supabase.table("users").select("google_drive_token").eq("id", user_id)
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
    Orbit writes goes somewhere under a single top-level "Orbit" folder
    (project-scoped items now go one level deeper still, see
    find_or_create_subfolder/get_or_create_project_drive_folder below;
    anything with no project — briefs, status updates — stays directly in
    this one). Looks for an existing one first so reconnecting after a
    disconnect reuses the same folder instead of creating a duplicate
    every time.
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


def _escape_drive_query_value(value: str) -> str:
    """Drive's `q` search syntax uses single-quoted string literals — a
    literal `'` or `\\` inside a folder/file name has to be backslash-escaped
    or it breaks (or hijacks) the query, same idea as escaping for SQL."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def find_or_create_subfolder(access_token: str, parent_folder_id: str, folder_name: str) -> str:
    """
    Same find-or-create shape as find_or_create_orbit_folder, one level
    deeper — used for a project's own subfolder inside the top-level Orbit
    folder (Orbit/{project name}/), so a project's task attachments and
    notes land somewhere addressable instead of piling into one flat
    folder together with every other project's.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    escaped_name = _escape_drive_query_value(folder_name)

    search = httpx.get(
        DRIVE_FILES_URL,
        headers=headers,
        params={
            "q": (
                f"name='{escaped_name}' and mimeType='application/vnd.google-apps.folder' "
                f"and trashed=false and '{parent_folder_id}' in parents"
            ),
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
        json={"name": folder_name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_folder_id]},
    )
    create.raise_for_status()
    return create.json()["id"]


def get_or_create_project_drive_folder(
    supabase: Client, access_token: str, root_folder_id: str, project_id: str, project_name: str
) -> str:
    """
    Returns a project's own Drive subfolder id, creating (and caching on
    projects.drive_folder_id) the first time the project ever writes
    anything to Drive. Every later call for the same project is just a
    cache read — no extra Drive round-trip once it exists.
    """
    project_result = execute_maybe_single(supabase.table("projects").select("drive_folder_id").eq("id", project_id))
    cached = project_result.data.get("drive_folder_id") if project_result.data else None
    if cached:
        return cached

    folder_id = find_or_create_subfolder(access_token, root_folder_id, project_name)
    supabase.table("projects").update({"drive_folder_id": folder_id}).eq("id", project_id).execute()
    return folder_id


def update_file_content(access_token: str, file_id: str, mime_type: str, content: bytes) -> None:
    """Overwrites an existing file's bytes in place — same media-upload PATCH
    upload_file uses for a brand new file, just pointed at a file_id that
    already exists instead of one just created. Used to keep a Drive copy
    in sync with something that gets edited after its first upload (e.g. a
    note), rather than accumulating a new file per edit."""
    response = httpx.patch(
        f"{DRIVE_UPLOAD_URL}/{file_id}",
        params={"uploadType": "media"},
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": mime_type},
        content=content,
    )
    response.raise_for_status()


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

    update_file_content(access_token, file_id, mime_type, content)
    return file_id
