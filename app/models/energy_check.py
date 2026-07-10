from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel

EnergyLevel = Literal["low", "medium", "high"]


class EnergyCheckCreate(BaseModel):
    level: EnergyLevel


class EnergyCheckResponse(BaseModel):
    id: UUID
    user_id: UUID
    level: str
    taken_at: datetime


class EnergyCheckListResponse(BaseModel):
    energy_checks: list[EnergyCheckResponse]


class EnergyCheckLatestResponse(BaseModel):
    energy_check: Optional[EnergyCheckResponse] = None
