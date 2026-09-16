from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.constants.common import PLATFORM_PATTERN


class HandoffRequest(BaseModel):
    sender_id: str
    plataforma: str = Field(..., pattern=PLATFORM_PATTERN)
    locked: bool
    reason: Optional[str] = None


class HandoffResponse(BaseModel):
    sender_id: str
    plataforma: str
    conversation_id: Optional[UUID] = None
    handoff_state: str
    handoff_until: Optional[datetime] = None
