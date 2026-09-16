from datetime import datetime, timezone
from typing import Optional

from app.models.report import (
    LeadConversationReportItem,
    LeadInteractionReportItem,
    LeadsReportFilters,
    LeadsReportPagination,
    LeadsReportResponse,
    LeadsReportSummary,
)
from app.db.connection import get_connection

_ALLOWED_ORDER_STATUS = {
    "draft",
    "collecting",
    "ready_to_confirm",
    "confirmed",
    "cancelled",
    "closed",
    "none",
}
_ALLOWED_CATEGORIES = {"lead", "proveedor", "saludo", "consulta", "spam", "auto_reply", "human_reply"}
_ALLOWED_HANDOFF = {"auto", "human_locked"}
_ALLOWED_OUTCOMES = {"all", "sale", "fallen", "discarded", "active", "unknown"}
_ALLOWED_SALE_DATA_STATUS = {"completos", "faltantes", "no_aplica"}


def _utc_naive(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _build_report_cte(
    *,
    from_date: Optional[datetime],
    to_date: Optional[datetime],
    plataforma: Optional[str],
    sender_id: Optional[str],
    sender: Optional[str],
    topic: Optional[str],
    search: Optional[str],
    order_status: Optional[str],
    categoria: Optional[str],
    handoff_state: Optional[str],
    only_nopal_sales: bool,
    sale_data_status: Optional[str],
    lead_outcome: str,
) -> tuple[str, list[object]]:
    params: list[object] = []
    base_conditions = ["1=1"]
    filtered_conditions = ["1=1"]

    if from_date is not None:
        params.append(from_date)
        base_conditions.append(f"conv.started_at >= ${len(params)}")

    if to_date is not None:
        params.append(to_date)
        base_conditions.append(f"conv.started_at <= ${len(params)}")

    if plataforma:
        params.append(plataforma)
        base_conditions.append(f"ci.plataforma = ${len(params)}")

    if sender_id:
        params.append(sender_id)
        base_conditions.append(f"ci.sender_id = ${len(params)}")

    if sender:
        params.append(f"%{sender}%")
        base_conditions.append(f"ci.sender_id ILIKE ${len(params)}")

    if handoff_state:
        params.append(handoff_state)
        base_conditions.append(f"conv.handoff_state = ${len(params)}")

    if search:
        params.append(f"%{search}%")
        base_conditions.append(
            f"("
            f"ci.sender_id ILIKE ${len(params)} OR "
            f"COALESCE(c.nombre, '') ILIKE ${len(params)} OR "
            f"COALESCE(c.telefono, '') ILIKE ${len(params)} OR "
            f"COALESCE(ord.ciudad, '') ILIKE ${len(params)} OR "
            f"COALESCE(last_intent.detected_topic, '') ILIKE ${len(params)}"
            f")"
        )

    if topic:
        params.append(f"%{topic}%")
        base_conditions.append(f"COALESCE(last_intent.detected_topic, '') ILIKE ${len(params)}")

    if categoria:
        params.append(categoria)
        base_conditions.append(
            f"EXISTS (SELECT 1 FROM messages mcat "
            f"WHERE mcat.conversation_id = conv.id AND mcat.categoria = ${len(params)})"
        )

    if order_status:
        if order_status == "none":
            filtered_conditions.append("order_status IS NULL")
        else:
            params.append(order_status)
            filtered_conditions.append(f"order_status = ${len(params)}")

    if lead_outcome != "all":
        params.append(lead_outcome)
        filtered_conditions.append(f"lead_outcome = ${len(params)}")

    if only_nopal_sales:
        filtered_conditions.append("is_nopal_sale = TRUE")

    if sale_data_status:
        params.append(sale_data_status)
        filtered_conditions.append(f"sale_data_status = ${len(params)}")

    cte = f"""
    WITH base AS (
        SELECT
            conv.id AS conversation_id,
            ci.contact_id,
            ci.sender_id,
            ci.plataforma,
            c.nombre,
            COALESCE(c.telefono, ord.telefono) AS telefono,
            conv.status AS conversation_status,
            conv.started_at,
            conv.last_message_at,
            conv.closed_at,
            conv.handoff_state,
            conv.handoff_source,
            conv.handoff_until,
            COALESCE(conv.clarification_attempts, 0) AS clarification_attempts,
            COALESCE(conv.topic_change_count, 0) AS topic_change_count,
            COALESCE(conv.interaction_closed, false) AS interaction_closed,
            last_intent.intent_category AS last_intent_category,
            last_intent.detected_topic AS last_detected_topic,
            ord.id AS order_id,
            ord.status AS order_status,
            ord.confirmed_by_openai,
            ord.cantidad,
            ord.ciudad,
            COALESCE(msg_stats.has_lead_category, false) AS is_nopal_sale,
            CASE
                WHEN NOT COALESCE(msg_stats.has_lead_category, false) THEN 'no_aplica'
                WHEN COALESCE(c.nombre, '') <> ''
                 AND COALESCE(COALESCE(c.telefono, ord.telefono), '') <> ''
                 AND COALESCE(ord.cantidad, '') <> ''
                 AND COALESCE(ord.ciudad, '') <> '' THEN 'completos'
                ELSE 'faltantes'
            END AS sale_data_status,
            CASE
                WHEN NOT COALESCE(msg_stats.has_lead_category, false) THEN ARRAY[]::text[]
                ELSE ARRAY_REMOVE(ARRAY[
                    CASE WHEN COALESCE(c.nombre, '') = '' THEN 'nombre' END,
                    CASE WHEN COALESCE(COALESCE(c.telefono, ord.telefono), '') = '' THEN 'telefono' END,
                    CASE WHEN COALESCE(ord.cantidad, '') = '' THEN 'cantidad' END,
                    CASE WHEN COALESCE(ord.ciudad, '') = '' THEN 'ciudad' END
                ], NULL)::text[]
            END AS missing_sale_fields,
            COALESCE(msg_stats.total_messages, 0) AS total_messages,
            COALESCE(msg_stats.inbound_messages, 0) AS inbound_messages,
            COALESCE(msg_stats.outbound_messages, 0) AS outbound_messages,
            CASE
                WHEN ord.status = 'confirmed' THEN 'sale'
                WHEN ord.status IN ('cancelled', 'closed') THEN 'fallen'
                WHEN ord.status IN ('draft', 'collecting', 'ready_to_confirm') THEN 'active'
                WHEN COALESCE(msg_stats.has_spam, false) OR COALESCE(msg_stats.has_proveedor, false) THEN 'discarded'
                ELSE 'unknown'
            END AS lead_outcome
        FROM conversations conv
        JOIN contact_identities ci ON ci.id = conv.contact_identity_id
        JOIN contacts c ON c.id = ci.contact_id
        LEFT JOIN LATERAL (
            SELECT
                o.id,
                o.status,
                o.confirmed_by_openai,
                o.telefono,
                o.cantidad,
                o.ciudad,
                o.closed_at,
                o.updated_at,
                o.created_at
            FROM orders o
            WHERE o.conversation_id = conv.id
            ORDER BY COALESCE(o.closed_at, o.updated_at, o.created_at) DESC
            LIMIT 1
        ) ord ON TRUE
        LEFT JOIN LATERAL (
            SELECT
                COUNT(*) AS total_messages,
                COUNT(*) FILTER (WHERE m.direction = 'inbound') AS inbound_messages,
                COUNT(*) FILTER (WHERE m.direction = 'outbound') AS outbound_messages,
                BOOL_OR(m.direction = 'inbound' AND m.categoria = 'spam') AS has_spam,
                BOOL_OR(m.direction = 'inbound' AND m.categoria = 'proveedor') AS has_proveedor,
                BOOL_OR(m.direction = 'inbound' AND m.categoria = 'lead') AS has_lead_category
            FROM messages m
            WHERE m.conversation_id = conv.id
        ) msg_stats ON TRUE
        LEFT JOIN LATERAL (
            SELECT m.intent_category, m.detected_topic
            FROM messages m
            WHERE m.conversation_id = conv.id
              AND m.direction = 'inbound'
            ORDER BY m.created_at DESC
            LIMIT 1
        ) last_intent ON TRUE
        WHERE {' AND '.join(base_conditions)}
    ),
    filtered AS (
        SELECT *
        FROM base
        WHERE {' AND '.join(filtered_conditions)}
    )
    """

    return cte, params


async def build_leads_report(
    *,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    plataforma: Optional[str] = None,
    sender_id: Optional[str] = None,
    sender: Optional[str] = None,
    topic: Optional[str] = None,
    search: Optional[str] = None,
    order_status: Optional[str] = None,
    categoria: Optional[str] = None,
    handoff_state: Optional[str] = None,
    only_nopal_sales: bool = False,
    sale_data_status: Optional[str] = None,
    lead_outcome: str = "all",
    include_interactions: bool = True,
    max_interactions: int = 30,
    limit: int = 50,
    offset: int = 0,
) -> LeadsReportResponse:
    from_date = _utc_naive(from_date)
    to_date = _utc_naive(to_date)

    if from_date and to_date and from_date > to_date:
        raise ValueError("from_date debe ser menor o igual a to_date")

    if order_status and order_status not in _ALLOWED_ORDER_STATUS:
        raise ValueError("order_status inválido")

    if categoria and categoria not in _ALLOWED_CATEGORIES:
        raise ValueError("categoria inválida")

    if handoff_state and handoff_state not in _ALLOWED_HANDOFF:
        raise ValueError("handoff_state inválido")

    if lead_outcome not in _ALLOWED_OUTCOMES:
        raise ValueError("lead_outcome inválido")

    if sale_data_status and sale_data_status not in _ALLOWED_SALE_DATA_STATUS:
        raise ValueError("sale_data_status inválido")

    cte, params = _build_report_cte(
        from_date=from_date,
        to_date=to_date,
        plataforma=plataforma,
        sender_id=sender_id,
        sender=sender,
        topic=topic,
        search=search,
        order_status=order_status,
        categoria=categoria,
        handoff_state=handoff_state,
        only_nopal_sales=only_nopal_sales,
        sale_data_status=sale_data_status,
        lead_outcome=lead_outcome,
    )

    async with get_connection() as conn:
        summary_row = await conn.fetchrow(
            cte
            + """
            SELECT
                COUNT(*)::int AS total_conversations,
                COUNT(*) FILTER (WHERE lead_outcome = 'sale')::int AS total_sales,
                COUNT(*) FILTER (WHERE lead_outcome = 'fallen')::int AS total_fallen,
                COUNT(*) FILTER (WHERE lead_outcome = 'discarded')::int AS total_discarded,
                COUNT(*) FILTER (WHERE lead_outcome = 'active')::int AS total_active,
                COUNT(*) FILTER (WHERE lead_outcome = 'unknown')::int AS total_unknown,
                COALESCE(SUM(inbound_messages), 0)::int AS total_inbound_messages,
                COALESCE(SUM(outbound_messages), 0)::int AS total_outbound_messages
            FROM filtered
            """,
            *params,
        )

        by_platform_rows = await conn.fetch(
            cte
            + """
            SELECT plataforma, COUNT(*)::int AS total
            FROM filtered
            GROUP BY plataforma
            ORDER BY total DESC
            """,
            *params,
        )

        by_order_status_rows = await conn.fetch(
            cte
            + """
            SELECT COALESCE(order_status, 'none') AS status, COUNT(*)::int AS total
            FROM filtered
            GROUP BY COALESCE(order_status, 'none')
            ORDER BY total DESC
            """,
            *params,
        )

        by_outcome_rows = await conn.fetch(
            cte
            + """
            SELECT lead_outcome, COUNT(*)::int AS total
            FROM filtered
            GROUP BY lead_outcome
            ORDER BY total DESC
            """,
            *params,
        )

        list_params = [*params, limit, offset]
        rows = await conn.fetch(
            cte
            + f"""
            SELECT *
            FROM filtered
            ORDER BY last_message_at DESC
            LIMIT ${len(params) + 1}
            OFFSET ${len(params) + 2}
            """,
            *list_params,
        )

        interactions_by_conversation: dict[object, list[LeadInteractionReportItem]] = {}
        if include_interactions and rows:
            conversation_ids = [row["conversation_id"] for row in rows]
            interaction_rows = await conn.fetch(
                """
                SELECT
                    conversation_id,
                    id AS message_id,
                    created_at,
                    direction,
                    categoria,
                    intent_category,
                    detected_topic,
                    texto,
                    external_message_id,
                    classified_by_openai
                FROM (
                    SELECT
                        m.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY m.conversation_id
                            ORDER BY m.created_at DESC
                        ) AS row_num
                    FROM messages m
                    WHERE m.conversation_id = ANY($1::uuid[])
                ) ranked
                WHERE ranked.row_num <= $2
                ORDER BY ranked.conversation_id, ranked.created_at ASC
                """,
                conversation_ids,
                max_interactions,
            )
            for item in interaction_rows:
                interactions_by_conversation.setdefault(item["conversation_id"], []).append(
                    LeadInteractionReportItem(
                        message_id=item["message_id"],
                        created_at=item["created_at"],
                        direction=item["direction"],
                        categoria=item["categoria"],
                        intent_category=item["intent_category"],
                        detected_topic=item["detected_topic"],
                        texto=item["texto"],
                        external_message_id=item["external_message_id"],
                        classified_by_openai=item["classified_by_openai"],
                    )
                )

    results = [
        LeadConversationReportItem(
            conversation_id=row["conversation_id"],
            contact_id=row["contact_id"],
            sender_id=row["sender_id"],
            plataforma=row["plataforma"],
            nombre=row["nombre"],
            telefono=row["telefono"],
            conversation_status=row["conversation_status"],
            started_at=row["started_at"],
            last_message_at=row["last_message_at"],
            closed_at=row["closed_at"],
            handoff_state=row["handoff_state"],
            handoff_source=row["handoff_source"],
            handoff_until=row["handoff_until"],
            clarification_attempts=row["clarification_attempts"],
            topic_change_count=row["topic_change_count"],
            interaction_closed=row["interaction_closed"],
            last_intent_category=row["last_intent_category"],
            last_detected_topic=row["last_detected_topic"],
            order_id=row["order_id"],
            order_status=row["order_status"],
            confirmed_by_openai=row["confirmed_by_openai"],
            cantidad=row["cantidad"],
            ciudad=row["ciudad"],
            is_nopal_sale=row["is_nopal_sale"],
            sale_data_status=row["sale_data_status"],
            missing_sale_fields=list(row["missing_sale_fields"] or []),
            lead_outcome=row["lead_outcome"],
            total_messages=row["total_messages"],
            inbound_messages=row["inbound_messages"],
            outbound_messages=row["outbound_messages"],
            interactions=interactions_by_conversation.get(row["conversation_id"], []),
        )
        for row in rows
    ]

    summary = LeadsReportSummary(
        total_conversations=summary_row["total_conversations"],
        total_sales=summary_row["total_sales"],
        total_fallen=summary_row["total_fallen"],
        total_discarded=summary_row["total_discarded"],
        total_active=summary_row["total_active"],
        total_unknown=summary_row["total_unknown"],
        total_inbound_messages=summary_row["total_inbound_messages"],
        total_outbound_messages=summary_row["total_outbound_messages"],
        by_platform={item["plataforma"]: item["total"] for item in by_platform_rows},
        by_order_status={item["status"]: item["total"] for item in by_order_status_rows},
        by_outcome={item["lead_outcome"]: item["total"] for item in by_outcome_rows},
    )

    filters = LeadsReportFilters(
        from_date=from_date,
        to_date=to_date,
        plataforma=plataforma,
        sender_id=sender_id,
        sender=sender,
        topic=topic,
        search=search,
        order_status=order_status,
        categoria=categoria,
        handoff_state=handoff_state,
        only_nopal_sales=only_nopal_sales,
        sale_data_status=sale_data_status,
        lead_outcome=lead_outcome,
        include_interactions=include_interactions,
        max_interactions=max_interactions,
    )

    pagination = LeadsReportPagination(limit=limit, offset=offset, returned=len(results))

    return LeadsReportResponse(
        generated_at=datetime.utcnow(),
        filters=filters,
        summary=summary,
        pagination=pagination,
        results=results,
    )
