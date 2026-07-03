from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    """What the Android app sends to POST /v1/projects."""

    name: str = Field(min_length=1, max_length=100)
    color: str = Field(default="#5BC0BE", pattern=r"^#[0-9A-Fa-f]{6}$")
    # Where the sun node lands on the map. Null is fine — the map screen
    # picks a sensible spot client-side when these aren't set, same as
    # the tap-to-place flow designed in the wireframes.
    map_x: Optional[float] = None
    map_y: Optional[float] = None


class ProjectUpdate(BaseModel):
    """
    PATCH /v1/projects/:id. Covers name/color changes (contract) plus
    map_x/map_y (added to the schema after the contract doc was written,
    for drag-to-reposition — worth reconciling the docs at some point,
    not blocking anything right now).
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    color: Optional[str] = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    map_x: Optional[float] = None
    map_y: Optional[float] = None


class ProjectResponse(BaseModel):
    id: UUID
    owner_id: UUID
    name: str
    color: str
    is_unsorted: bool
    map_x: Optional[float] = None
    map_y: Optional[float] = None
    archived_at: Optional[datetime] = None
    drive_folder_id: Optional[str] = None
    created_at: datetime


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse]
