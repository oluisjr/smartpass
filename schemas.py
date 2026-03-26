from pydantic import BaseModel
from datetime import datetime

class SmartPassCreate(BaseModel):
    name: str
    company: str
    event: str
    valid_from: datetime
    valid_to: datetime

class CheckInRequest(BaseModel):
    smartpass_id: str
    reader_id: str

class CheckInResponse(BaseModel):
    status: str
    name: str | None = None
    company: str | None = None
    event: str | None = None
    reason: str | None = None
