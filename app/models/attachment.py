from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class AttachmentResponse(BaseModel):
    id: UUID
    task_id: UUID
    user_id: UUID
    file_name: str
    mime_type: str
    size_bytes: int
    drive_file_id: Optional[str] = None
    created_at: datetime


class AttachmentListResponse(BaseModel):
    attachments: list[AttachmentResponse]
