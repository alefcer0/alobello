from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class LeadInteractionReportItem(BaseModel):
    message_id: UUID
    created_at: datetime
    direction: str
    categoria: Optional[str] = None
    intent_category: Optional[str] = None
    detected_topic: Optional[str] = None
    texto: Optional[str] = None
    external_message_id: Optional[str] = None
    classified_by_openai: bool = False


class LeadConversationReportItem(BaseModel):
    conversation_id: UUID
    contact_id: Optional[UUID] = None
    sender_id: str
    plataforma: str
    nombre: Optional[str] = None
    telefono: Optional[str] = None
    conversation_status: str
    started_at: datetime
    last_message_at: datetime
    closed_at: Optional[datetime] = None
    handoff_state: Optional[str] = None
    handoff_source: Optional[str] = None
    handoff_until: Optional[datetime] = None
    clarification_attempts: int = 0
    topic_change_count: int = 0
    interaction_closed: bool = False
    last_intent_category: Optional[str] = None
    last_detected_topic: Optional[str] = None
    order_id: Optional[UUID] = None
    order_status: Optional[str] = None
    confirmed_by_openai: Optional[bool] = None
    cantidad: Optional[str] = None
    ciudad: Optional[str] = None
    is_nopal_sale: bool = False
    sale_data_status: str = "no_aplica"
    missing_sale_fields: list[str] = Field(default_factory=list)
    lead_outcome: str
    total_messages: int
    inbound_messages: int
    outbound_messages: int
    interactions: list[LeadInteractionReportItem] = Field(default_factory=list)


class LeadsReportSummary(BaseModel):
    total_conversations: int
    total_sales: int
    total_fallen: int
    total_discarded: int
    total_active: int
    total_unknown: int
    total_inbound_messages: int
    total_outbound_messages: int
    by_platform: dict[str, int] = Field(default_factory=dict)
    by_order_status: dict[str, int] = Field(default_factory=dict)
    by_outcome: dict[str, int] = Field(default_factory=dict)


class LeadsReportFilters(BaseModel):
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None
    plataforma: Optional[str] = None
    sender_id: Optional[str] = None
    sender: Optional[str] = None
    topic: Optional[str] = None
    search: Optional[str] = None
    order_status: Optional[str] = None
    categoria: Optional[str] = None
    handoff_state: Optional[str] = None
    only_nopal_sales: bool = False
    sale_data_status: Optional[str] = None
    lead_outcome: str = "all"
    include_interactions: bool = True
    max_interactions: int = 30


class LeadsReportPagination(BaseModel):
    limit: int
    offset: int
    returned: int


class LeadsReportResponse(BaseModel):
    generated_at: datetime
    filters: LeadsReportFilters
    summary: LeadsReportSummary
    pagination: LeadsReportPagination
    results: list[LeadConversationReportItem]
