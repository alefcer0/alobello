from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AuthLoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=120)
    password: str = Field(..., min_length=8, max_length=256)


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    expires_in_seconds: int
    username: str
    is_superuser: bool


class AuthenticatedUser(BaseModel):
    user_id: UUID
    username: str
    is_superuser: bool
