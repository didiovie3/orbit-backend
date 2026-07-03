from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class NoteCreate(BaseModel):
    """What the Android app sends to POST /v1/notes."""

    project_id: Optional[UUID] = None
    title: Optional[str] = Field(default=None, max_length=200)
    content: str = Field(min_length=1)


class NoteUpdate(BaseModel):
    """PATCH /v1/notes/:id — every field optional, send only what changed."""

    project_id: Optional[UUID] = None
    title: Optional[str] = Field(default=None, max_length=200)
    content: Optional[str] = Field(default=None, min_length=1)


class NoteResponse(BaseModel):
    id: UUID
    user_id: UUID
    project_id: Optional[UUID] = None
    title: Optional[str] = None
    content: str
    summary: Optional[str] = None
    drive_file_id: Optional[str] = None
    archived_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class NoteListResponse(BaseModel):
    notes: list[NoteResponse]
