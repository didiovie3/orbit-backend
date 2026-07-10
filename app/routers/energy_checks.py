from fastapi import APIRouter, Depends, status
from supabase import Client

from app.core.auth import CurrentUser, get_current_user
from app.db.supabase_client import get_supabase
from app.models.energy_check import (
    EnergyCheckCreate,
    EnergyCheckLatestResponse,
    EnergyCheckListResponse,
    EnergyCheckResponse,
)

router = APIRouter(prefix="/energy-checks", tags=["energy-checks"])


@router.post("", response_model=EnergyCheckResponse, status_code=status.HTTP_201_CREATED)
def create_energy_check(
    body: EnergyCheckCreate,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    row = {"user_id": current_user.user_id, "level": body.level}
    result = supabase.table("energy_checks").insert(row).execute()
    return result.data[0]


@router.get("/latest", response_model=EnergyCheckLatestResponse)
def get_latest_energy_check(
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    # Most recent check regardless of date — this deliberately doesn't try to
    # answer "is this today's" itself (no timezone-aware "is this today"
    # check exists anywhere else in this codebase either); callers compare
    # taken_at against their own local calendar day, same as the Android
    # app's SharedPreferences-based once-a-day gate already does.
    result = (
        supabase.table("energy_checks")
        .select("*")
        .eq("user_id", current_user.user_id)
        .order("taken_at", desc=True)
        .limit(1)
        .execute()
    )
    return {"energy_check": result.data[0] if result.data else None}


@router.get("", response_model=EnergyCheckListResponse)
def list_energy_checks(
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    result = (
        supabase.table("energy_checks")
        .select("*")
        .eq("user_id", current_user.user_id)
        .order("taken_at", desc=True)
        .execute()
    )
    return {"energy_checks": result.data}
