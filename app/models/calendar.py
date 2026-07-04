from pydantic import BaseModel


class ConnectCalendarRequest(BaseModel):
    auth_code: str


class ConnectCalendarResponse(BaseModel):
    connected: bool
    calendar_email: str


class SyncResponse(BaseModel):
    pushed: int
    pulled: int
    synced_at: str
    connected: bool
