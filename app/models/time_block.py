from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class TimeBlockCreate(BaseModel):
    project_id: UUID  # required — PRD §5.9: "a time block is always tied to one project"
    title: str
    start_at: datetime
    end_at: datetime
    note: Optional[str] = None


class TimeBlockUpdate(BaseModel):
    project_id: Optional[UUID] = None
    title: Optional[str] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    note: Optional[str] = None


class TimeBlockResponse(BaseModel):
    id: UUID
    user_id: UUID
    project_id: UUID
    title: str
    start_at: datetime
    end_at: datetime
    note: Optional[str] = None
    calendar_event_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class TimeBlockListResponse(BaseModel):
    time_blocks: list[TimeBlockResponse]
