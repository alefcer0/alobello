from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ClassifyRequest(BaseModel):
    texto: str


class ClassifyResponse(BaseModel):
    categoria: str


class ExtractRequest(BaseModel):
    texto: str


class ExtractResponse(BaseModel):
    telefono: Optional[str] = None
    cantidad: Optional[str] = None
    ciudad: Optional[str] = None


class RespondRequest(BaseModel):
    sender_id: str
    plataforma: str = Field(..., pattern="^(facebook|instagram|whatsapp)$")
    texto: Optional[str] = None
    session_id: Optional[UUID] = None


class RespondResponse(BaseModel):
    mensaje: str
    enviado: bool
    campos_faltantes: list[str] = []


class LeadResponse(BaseModel):
    id: UUID
    session_id: UUID
    fecha: datetime
    plataforma: Optional[str] = None
    tipo: Optional[str] = None
    mensaje: Optional[str] = None
    nombre: Optional[str] = None
    telefono: Optional[str] = None
    cantidad: Optional[str] = None
    ciudad: Optional[str] = None
    datos_extraidos_en: Optional[str] = None
