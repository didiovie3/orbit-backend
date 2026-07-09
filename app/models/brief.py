from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel

BriefType = Literal["morning", "progress", "eod"]


class GenerateBriefRequest(BaseModel):
    brief_type: BriefType


class BriefResponse(BaseModel):
    id: UUID
    user_id: UUID
    brief_type: BriefType
    content: str
    drive_file_id: Optional[str] = None
    generated_at: datetime


class BriefListResponse(BaseModel):
    briefs: list[BriefResponse]
