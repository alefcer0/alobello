from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.constants.common import PLATFORM_PATTERN


class SessionCreate(BaseModel):
    sender_id: str
    plataforma: str = Field(..., pattern=PLATFORM_PATTERN)


class SessionResponse(BaseModel):
    id: UUID
    contact_id: Optional[UUID] = None
    sender_id: str
    plataforma: Optional[str] = None
    ultimo_contacto: datetime
    activa: bool
    telefono: Optional[str] = None
    cantidad: Optional[str] = None
    ciudad: Optional[str] = None
    nombre: Optional[str] = None
    handoff_state: Optional[str] = None
    created: bool = False


class ConversationFlowState(BaseModel):
    clarification_attempts: int = 0
    topic_change_count: int = 0
    current_topic: Optional[str] = None
    interaction_closed: bool = False
