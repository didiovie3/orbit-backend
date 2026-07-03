from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ── What we ask Gemini to return ─────────────────────────────────────────
# These two are passed directly as response_schema to the Gemini API —
# it's genuinely constrained to return exactly this shape. We still
# re-validate everything ourselves afterward rather than trusting it
# blindly (see extraction.py) — schema adherence is strong but not
# guaranteed to be perfect, and this is still untrusted external input.

class ExtractedSubTask(BaseModel):
    label: str
    base_urgency: int = Field(ge=1, le=5)
    due_at: Optional[str] = None  # ISO8601 string or null; Gemini can't return a real datetime type


class ExtractedTask(BaseModel):
    label: str
    base_urgency: int = Field(ge=1, le=5)
    due_at: Optional[str] = None
    sub_tasks: list[ExtractedSubTask] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    transcript: str
    hierarchy: list[ExtractedTask]


# ── API request/response shapes ──────────────────────────────────────────

class CaptureVoiceResponse(BaseModel):
    """What POST /v1/capture/voice returns — nothing is saved yet."""

    transcript: str
    hierarchy: list[ExtractedTask]
    voice_log_id: UUID


class ConfirmHierarchyItem(BaseModel):
    """
    Same shape as ExtractedTask, but this is what the ANDROID APP sends
    back to /confirm — after the user has possibly edited it. Separate
    model from ExtractedTask on purpose: that one is Gemini's contract,
    this one is the client's contract. They happen to look identical
    today, but they're allowed to drift independently later.
    """

    label: str = Field(min_length=1)
    base_urgency: int = Field(ge=1, le=5)
    due_at: Optional[datetime] = None
    sub_tasks: list["ConfirmHierarchyItem"] = Field(default_factory=list)


class ConfirmCaptureRequest(BaseModel):
    voice_log_id: Optional[UUID] = None
    image_log_id: Optional[UUID] = None
    note_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    audience_id: Optional[UUID] = None
    location: Optional[str] = None
    hierarchy: list[ConfirmHierarchyItem]
