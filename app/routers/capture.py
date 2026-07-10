from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from supabase import Client

from app.core.auth import CurrentUser, get_current_user
from app.db.supabase_client import execute_maybe_single, get_supabase
from app.models.capture import CaptureImageResponse, CaptureVoiceResponse, ConfirmCaptureRequest
from app.models.task import TaskResponse
from app.services.audiences import is_owned_audience
from app.services.gemini_service import extract_hierarchy_from_audio, extract_hierarchy_from_image
from app.services.projects import is_owned_project
from app.services.task_creation import save_hierarchy_as_tasks

router = APIRouter(prefix="/capture", tags=["capture"])

ALLOWED_AUDIO_TYPES = {"audio/mp4", "audio/m4a", "audio/x-m4a", "audio/mpeg", "audio/mp3"}
MAX_AUDIO_BYTES = 25 * 1024 * 1024  # 25MB — generous for a voice memo under 60s

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png"}
MAX_IMAGE_BYTES = 15 * 1024 * 1024  # 15MB — a phone photo of a whiteboard fits comfortably


@router.post("/voice", response_model=CaptureVoiceResponse, status_code=status.HTTP_201_CREATED)
async def capture_voice(
    audio: UploadFile = File(...),
    project_id: str | None = Form(default=None),
    audience_id: str | None = Form(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    if audio.content_type not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported audio type '{audio.content_type}'. Expected .m4a or .mp3.",
        )

    audio_bytes = await audio.read()
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Audio file too large (max 25MB).",
        )
    if len(audio_bytes) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty audio file.")

    try:
        result = extract_hierarchy_from_audio(audio_bytes, audio.content_type)
    except Exception as exc:
        # Covers: Gemini API errors, network issues, and the model
        # returning something that doesn't parse as our schema. All of
        # these are "the AI step failed," not "the client sent something
        # wrong" — 502 (Bad Gateway) reflects that distinction, rather
        # than lumping it in with a validation error.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI extraction failed: {exc}",
        ) from exc

    # audio_ref would normally point at where the raw audio got persisted
    # in Supabase Storage — that bucket/upload flow isn't built yet (same
    # "not built yet, stays a placeholder" situation as drive_file_id).
    # The transcript and extraction still work fully without it.
    voice_log_row = {
        "user_id": current_user.user_id,
        "audio_ref": f"pending-storage:{audio.filename}",
        "transcript": result.transcript,
        "duration_seconds": None,
    }
    log_result = supabase.table("voice_logs").insert(voice_log_row).execute()
    voice_log = log_result.data[0]

    return {
        "transcript": result.transcript,
        "hierarchy": result.hierarchy,
        "voice_log_id": voice_log["id"],
    }


@router.post("/image", response_model=CaptureImageResponse, status_code=status.HTTP_201_CREATED)
async def capture_image(
    image: UploadFile = File(...),
    project_id: str | None = Form(default=None),
    audience_id: str | None = Form(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    if image.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported image type '{image.content_type}'. Expected .jpg or .png.",
        )

    image_bytes = await image.read()
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Image file too large (max 15MB).",
        )
    if len(image_bytes) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty image file.")

    try:
        result = extract_hierarchy_from_image(image_bytes, image.content_type)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI extraction failed: {exc}",
        ) from exc

    # Same "not built yet" placeholder as voice_ref — Supabase Storage
    # upload isn't wired up. OCR and extraction work fully without it.
    image_log_row = {
        "user_id": current_user.user_id,
        "image_ref": f"pending-storage:{image.filename}",
        "ocr_text": result.transcript,  # ExtractionResult's field is still named .transcript internally — see gemini_service.py's docstring on extract_hierarchy_from_image for why
    }
    log_result = supabase.table("image_logs").insert(image_log_row).execute()
    image_log = log_result.data[0]

    return {
        "ocr_text": result.transcript,
        "hierarchy": result.hierarchy,
        "image_log_id": image_log["id"],
    }


@router.post("/confirm", response_model=list[TaskResponse], status_code=status.HTTP_201_CREATED)
def confirm_capture(
    body: ConfirmCaptureRequest,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    if body.voice_log_id:
        source = "voice"
    elif body.image_log_id:
        source = "image"
    else:
        source = "typed"  # e.g. confirming tasks extracted from a note's text

    if body.audience_id and not is_owned_audience(supabase, str(body.audience_id), current_user.user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audience not found")

    if body.project_id and not is_owned_project(supabase, str(body.project_id), current_user.user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # Same ownership gap as project_id/audience_id above — without this, a
    # client could link its new tasks to another user's voice/image/note
    # log via the join tables in save_hierarchy_as_tasks below.
    if body.voice_log_id:
        result = execute_maybe_single(
            supabase.table("voice_logs").select("id").eq("id", str(body.voice_log_id)).eq("user_id", current_user.user_id)
        )
        if not result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Voice log not found")

    if body.image_log_id:
        result = execute_maybe_single(
            supabase.table("image_logs").select("id").eq("id", str(body.image_log_id)).eq("user_id", current_user.user_id)
        )
        if not result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image log not found")

    if body.note_id:
        result = execute_maybe_single(
            supabase.table("notes").select("id").eq("id", str(body.note_id)).eq("user_id", current_user.user_id)
        )
        if not result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")

    created = save_hierarchy_as_tasks(
        supabase=supabase,
        user_id=current_user.user_id,
        hierarchy=body.hierarchy,
        project_id=str(body.project_id) if body.project_id else None,
        audience_id=str(body.audience_id) if body.audience_id else None,
        location=body.location,
        source=source,
        voice_log_id=str(body.voice_log_id) if body.voice_log_id else None,
        image_log_id=str(body.image_log_id) if body.image_log_id else None,
        note_id=str(body.note_id) if body.note_id else None,
    )
    return created
