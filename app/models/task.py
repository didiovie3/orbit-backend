from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.common import DeleteConfirm  # noqa: F401 — re-exported so existing `from app.models.task import DeleteConfirm` still works

# These value sets are used in multiple places below — defined once here so
# they can't drift out of sync with each other or with the database's own
# CHECK constraints.
TASK_STATUSES = ("overdue", "today", "later", "done")
TASK_SOURCES = ("typed", "voice", "image")
# Manually-set progress, distinct from status: status is derived from due_at
# (deriveStatusFromDueAt on the client) and only "done" is meaningful there.
# progress is the "not started / in progress / almost done / done" the
# Update Progress sheet actually offers — matches PROGRESS_OPTIONS in
# ReminderInteractionSheets.kt on the client, and the tasks.progress CHECK
# constraint in the database.
TASK_PROGRESS_VALUES = ("not_started", "in_progress", "almost_done", "done")


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
    location: Optional[str] = None
    progress: Optional[str] = None

    @field_validator("status")
    @classmethod
    def status_must_be_known(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in TASK_STATUSES:
            raise ValueError(f"status must be one of {TASK_STATUSES}")
        return v

    @field_validator("progress")
    @classmethod
    def progress_must_be_known(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in TASK_PROGRESS_VALUES:
            raise ValueError(f"progress must be one of {TASK_PROGRESS_VALUES}")
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
    progress: str
    source: str
    due_at: Optional[datetime] = None
    calendar_event_id: Optional[str] = None
    # Position among siblings sharing the same parent_task_id — only
    # meaningful for sub-tasks. Assigned server-side (append-on-create,
    # rewritten wholesale by PATCH /tasks/:id/subtasks/reorder), never
    # taken directly from the client.
    sort_index: int
    archived_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]


class SubtaskReorderRequest(BaseModel):
    """Ordered list of sub-task ids — position in this list becomes each
    one's new sort_index. All ids must already be sub-tasks of the parent
    in the URL and owned by the caller."""

    task_ids: list[UUID]


class SetRemindersRequest(BaseModel):
    remind_at_list: list[datetime]


class ReminderResponse(BaseModel):
    id: UUID
    task_id: UUID
    remind_at: datetime


class RemindersListResponse(BaseModel):
    reminders: list[ReminderResponse]
