from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class GenerateStatusUpdateRequest(BaseModel):
    audience_id: UUID


class SendStatusUpdateRequest(BaseModel):
    sent_text: str


class StatusUpdateResponse(BaseModel):
    id: UUID
    audience_id: UUID
    draft_text: str
    sent_text: Optional[str] = None
    sent_at: Optional[datetime] = None
    drive_file_id: Optional[str] = None
    task_ids: list[UUID]
    created_at: datetime
