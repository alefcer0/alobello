from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    sender_id: str
    plataforma: str = Field(..., pattern="^(facebook|instagram|whatsapp)$")


class SessionResponse(BaseModel):
    id: UUID
    sender_id: str
    plataforma: Optional[str] = None
    ultimo_contacto: datetime
    activa: bool
    telefono: Optional[str] = None
    cantidad: Optional[str] = None
    ciudad: Optional[str] = None
    nombre: Optional[str] = None
    created: bool = False
