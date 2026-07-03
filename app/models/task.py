from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.common import DeleteConfirm  # noqa: F401 — re-exported so existing `from app.models.task import DeleteConfirm` still works

# These four status/source value sets are used in multiple places below —
# defined once here so they can't drift out of sync with each other or
# with the database's own CHECK constraints.
TASK_STATUSES = ("overdue", "today", "later", "done")
TASK_SOURCES = ("typed", "voice", "image")


class TaskCreate(BaseModel):
    """What the Android app sends to POST /v1/tasks."""

    project_id: Optional[UUID] = None
    parent_task_id: Optional[UUID] = None
    audience_id: Optional[UUID] = None
    label: str = Field(min_length=1, max_length=500)
    location: Optional[str] = None
    base_urgency: int = Field(default=2, ge=1, le=5)
    due_at: Optional[datetime] = None
    escalation_enabled: bool = True
    source: str = Field(default="typed")

    @field_validator("source")
    @classmethod
    def source_must_be_known(cls, v: str) -> str:
        if v not in TASK_SOURCES:
            raise ValueError(f"source must be one of {TASK_SOURCES}")
        return v


class TaskUpdate(BaseModel):
    """
    What the Android app sends to PATCH /v1/tasks/:id.
    Every field is optional — the contract's rule is 'send only changed
    fields', so we can't require anything here the way TaskCreate does.
    """

    project_id: Optional[UUID] = None
    parent_task_id: Optional[UUID] = None
    audience_id: Optional[UUID] = None
    label: Optional[str] = Field(default=None, min_length=1, max_length=500)
    base_urgency: Optional[int] = Field(default=None, ge=1, le=5)
    escalation_enabled: Optional[bool] = None
    status: Optional[str] = None
    due_at: Optional[datetime] = None

    @field_validator("status")
    @classmethod
    def status_must_be_known(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in TASK_STATUSES:
            raise ValueError(f"status must be one of {TASK_STATUSES}")
        return v


class TaskResponse(BaseModel):
    """What every task endpoint returns — the full row, shaped consistently."""

    id: UUID
    user_id: UUID
    project_id: Optional[UUID] = None
    parent_task_id: Optional[UUID] = None
    audience_id: Optional[UUID] = None
    label: str
    location: Optional[str] = None
    base_urgency: int
    urgency: int
    escalation_enabled: bool
    status: str
    source: str
    due_at: Optional[datetime] = None
    calendar_event_id: Optional[str] = None
    archived_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]
