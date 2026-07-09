from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel

InteractionType = Literal["progress", "reschedule", "ignored"]
ProgressUpdate = Literal["not_started", "in_progress", "almost_done", "completed"]


class ReminderEventUpdate(BaseModel):
    interaction_type: Optional[InteractionType] = None
    progress_update: Optional[ProgressUpdate] = None
    dismiss_reason: Optional[str] = None
    rescheduled_to: Optional[datetime] = None


class ReminderEventResponse(BaseModel):
    id: UUID
    task_id: UUID
    scheduled_for: datetime
    fired_at: Optional[datetime] = None
    nudge_count: int
    interaction_type: Optional[str] = None
    progress_update: Optional[str] = None
    dismiss_reason: Optional[str] = None
    rescheduled_to: Optional[datetime] = None
