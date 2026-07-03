from typing import Optional
from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, Field


class AudienceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    tone_notes: Optional[str] = None


class AudienceUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    tone_notes: Optional[str] = None


class AudienceResponse(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    tone_notes: Optional[str] = None
    created_at: datetime


class AudienceListResponse(BaseModel):
    audiences: list[AudienceResponse]
