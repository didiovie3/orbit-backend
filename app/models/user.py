from typing import Optional

from pydantic import BaseModel, Field


class UserResponse(BaseModel):
    id: str
    name: str
    today_task_limit: int
    dynamic_escalation_enabled: bool
    briefs_enabled: bool
    morning_brief_time: str
    progress_brief_times: list[str]
    eod_brief_time: str
    writing_style_notes: Optional[str] = None
    # Derived, not a raw column — tells the client whether Calendar is
    # connected without ever exposing the actual stored OAuth token.
    calendar_connected: bool
    created_at: str


class UserUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    today_task_limit: Optional[int] = Field(default=None, ge=1, le=10)
    dynamic_escalation_enabled: Optional[bool] = None
    briefs_enabled: Optional[bool] = None
    morning_brief_time: Optional[str] = None
    progress_brief_times: Optional[list[str]] = None
    eod_brief_time: Optional[str] = None
    writing_style_notes: Optional[str] = None
    # Lets the Android app register/refresh its push token here, rather
    # than needing a separate endpoint just for this one field.
    fcm_token: Optional[str] = None
