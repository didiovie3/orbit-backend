from pydantic import BaseModel


class ConnectDriveRequest(BaseModel):
    auth_code: str


class ConnectDriveResponse(BaseModel):
    connected: bool
    root_folder_id: str
