import json
import logging
import re
import asyncio
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from app.constants.common import (
    ACTIVE_ORDER_STATUSES,
    CATEGORY_LEAD,
    CATEGORY_SALUDO,
    CONVERSATION_STATUS_CLOSED,
    CONVERSATION_STATUS_OPEN,
    DATOS_EXTRAIDOS_PRIMER_MENSAJE,
    DATOS_EXTRAIDOS_RESPUESTA,
    DEFAULT_QUANTITY_UNIT,
    DIRECTION_INBOUND,
    FIELD_CANTIDAD,
    FIELD_CIUDAD,
    FIELD_NOMBRE,
    FIELD_TELEFONO,
    FINAL_ORDER_STATUSES,
    ORDER_CAPTURE_NOTES_TEMPLATE,
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_CLOSED,
    ORDER_STATUS_DRAFT,
    ORDER_STATUS_COLLECTING,
    ORDER_STATUS_CONFIRMED,
    ORDER_STATUS_READY_TO_CONFIRM,
    PRODUCT_CODE_NOPAL,
    PRODUCT_NAME_NOPAL,
)
from app.constants.handoff import (
    HANDOFF_SOURCE_AUTO,
    HANDOFF_SOURCE_MANUAL,
    HANDOFF_STATE_AUTO,
    HANDOFF_STATE_HUMAN_LOCKED,
)
from app.constants.handoff_runtime import (
    LOG_HANDOFF_AUTO_EXPIRED_TEMPLATE,
    LOG_HANDOFF_LOCK_TEMPLATE,
    LOG_HANDOFF_MARK_INTERVENTION_TEMPLATE,
    LOG_HANDOFF_UNLOCK_TEMPLATE,
)
from app.constants.session import ORDER_CANCELLATION_KEYWORDS, ORDER_CONFIRMATION_KEYWORDS
from app.constants.session_runtime import (
    ERROR_CONVERSATION_NOT_FOUND_TEMPLATE,
    LOG_ACTIVE_CONVERSATION_TEMPLATE,
    LOG_CONVERSATION_RENEWED_TEMPLATE,
    LOG_GET_OR_CREATE_SESSION_TEMPLATE,
    LOG_MERGED_SESSION_TEMPLATE,
    LOG_MERGE_DATA_TEMPLATE,
    LOG_MESSAGE_SAVED_TEMPLATE,
    LOG_NEW_CONVERSATION_TEMPLATE,
    ORDER_INTENT_CANCELLED,
    ORDER_INTENT_CONFIRMED,
    QUANTITY_PARSE_REGEX,
    WHITESPACE_REGEX,
)
from app.constants.units import normalize_nopal_unit
from app.config import settings
from app.db.connection import get_connection
from app.models.lead import ExtractResponse
from app.models.session import ConversationFlowState, SessionResponse

logger = logging.getLogger(__name__)
_handoff_schema_ready = False
_handoff_schema_lock = asyncio.Lock()


def _normalize_text(text: str) -> str:
    return re.sub(WHITESPACE_REGEX, " ", text.lower()).strip()


def _normalize_topic_value(topic: Optional[str]) -> Optional[str]:
    if not topic:
        return None
    cleaned = re.sub(WHITESPACE_REGEX, " ", topic.lower()).strip(" .,:;!?\"'")
    return cleaned[:160] or None


def _is_generic_topic(topic: Optional[str]) -> bool:
    return (topic or "") in {"consulta general", "tu consulta"}


def _contains_intent_phrase(text: str, keywords: tuple[str, ...]) -> bool:
    for keyword in keywords:
        token = keyword.strip().lower()
        if " " in token:
            if token in text:
                return True
            continue
        if re.search(rf"\b{re.escape(token)}\b", text):
            return True
    return False


def _idle_expiry_cutoff() -> datetime:
    minutes = settings.session_idle_timeout_minutes
    if minutes <= 0:
        return datetime.utcnow() - timedelta(hours=settings.session_expiry_hours)
    return datetime.utcnow() - timedelta(minutes=minutes)


def _max_duration_cutoff() -> datetime:
    minutes = settings.session_max_duration_minutes
    if minutes <= 0:
        return datetime.utcnow() - timedelta(hours=settings.session_expiry_hours)
    return datetime.utcnow() - timedelta(minutes=minutes)


def _handoff_until_from_now() -> Optional[datetime]:
    minutes = settings.handoff_idle_timeout_minutes
    if minutes <= 0:
        return None
    return datetime.utcnow() + timedelta(minutes=minutes)


def _is_conversation_expired(last_message_at: Optional[datetime], started_at: Optional[datetime]) -> bool:
    if started_at and started_at < _max_duration_cutoff():
        return True
    if last_message_at and last_message_at < _idle_expiry_cutoff():
        return True
    return False


def _parse_quantity_text(cantidad: Optional[str]) -> tuple[Optional[float], Optional[str], Optional[str]]:
    if not cantidad:
        return None, None, None

    normalized = cantidad.replace(",", ".").strip()
    match = re.search(QUANTITY_PARSE_REGEX, normalized)
    if not match:
        return None, None, normalized

    try:
        quantity = float(match.group(1))
    except ValueError:
        return None, None, normalized

    unit = normalize_nopal_unit(match.group(2)) or DEFAULT_QUANTITY_UNIT
    return quantity, unit, None


def _format_item_quantity(quantity: Optional[float], unit: Optional[str]) -> Optional[str]:
    if quantity is None:
        return None
    value = int(quantity) if float(quantity).is_integer() else quantity
    return f"{value} {unit or DEFAULT_QUANTITY_UNIT}"


async def _upsert_primary_order_item(conn, order_id: UUID, cantidad: Optional[str]) -> None:
    qty, unit, notes = _parse_quantity_text(cantidad)
    await conn.execute(
        """
        INSERT INTO order_items (
            order_id,
            product_code,
            product_name,
            quantity,
            unit,
            notes,
            created_at,
            updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW())
        ON CONFLICT (order_id, product_code)
        DO UPDATE
        SET quantity = COALESCE(EXCLUDED.quantity, order_items.quantity),
            unit = COALESCE(EXCLUDED.unit, order_items.unit),
            notes = COALESCE(EXCLUDED.notes, order_items.notes),
            updated_at = NOW()
        """,
        order_id,
        PRODUCT_CODE_NOPAL,
        PRODUCT_NAME_NOPAL,
        qty,
        unit,
        notes,
    )


async def _close_conversation(conn, conversation_id: UUID) -> None:
    await conn.execute(
        """
        UPDATE conversations
        SET status = $2, closed_at = COALESCE(closed_at, NOW())
        WHERE id = $1
        """,
        conversation_id,
        CONVERSATION_STATUS_CLOSED,
    )


async def _deactivate_expired(conn) -> None:
    idle_cutoff = _idle_expiry_cutoff()
    max_duration_cutoff = _max_duration_cutoff()

    await conn.execute(
        """
        UPDATE conversations conv
                SET status = $2, closed_at = COALESCE(closed_at, NOW())
                WHERE conv.status = $3
          AND EXISTS (
            SELECT 1
            FROM orders ord
            WHERE ord.conversation_id = conv.id
              AND ord.status = ANY($1::text[])
          )
        """,
        list(FINAL_ORDER_STATUSES),
        CONVERSATION_STATUS_CLOSED,
        CONVERSATION_STATUS_OPEN,
    )

    await conn.execute(
        """
        UPDATE conversations
        SET status = $2, closed_at = COALESCE(closed_at, NOW())
        WHERE status = $3 AND started_at < $1
        """,
        max_duration_cutoff,
        CONVERSATION_STATUS_CLOSED,
        CONVERSATION_STATUS_OPEN,
    )

    await conn.execute(
        """
        UPDATE conversations
        SET status = $2, closed_at = COALESCE(closed_at, NOW())
        WHERE status = $3 AND last_message_at < $1
        """,
        idle_cutoff,
        CONVERSATION_STATUS_CLOSED,
        CONVERSATION_STATUS_OPEN,
    )

    await conn.execute(
        """
        UPDATE orders ord
                SET status = $2, closed_at = COALESCE(closed_at, NOW()), updated_at = NOW()
        WHERE ord.status = ANY($1::text[])
          AND EXISTS (
            SELECT 1
            FROM conversations conv
            WHERE conv.id = ord.conversation_id
                            AND conv.status = $2
          )
        """,
        list(ACTIVE_ORDER_STATUSES),
        CONVERSATION_STATUS_CLOSED,
    )


async def _ensure_handoff_schema(conn) -> None:
    global _handoff_schema_ready

    if _handoff_schema_ready:
        return

    async with _handoff_schema_lock:
        if _handoff_schema_ready:
            return

        await conn.execute(
            """
            ALTER TABLE conversations
            ADD COLUMN IF NOT EXISTS handoff_state VARCHAR(20) NOT NULL DEFAULT 'auto'
            """
        )
        await conn.execute(
            """
            ALTER TABLE conversations
            ADD COLUMN IF NOT EXISTS handoff_source VARCHAR(30)
            """
        )
        await conn.execute(
            """
            ALTER TABLE conversations
            ADD COLUMN IF NOT EXISTS handoff_locked_at TIMESTAMP
            """
        )
        await conn.execute(
            """
            ALTER TABLE conversations
            ADD COLUMN IF NOT EXISTS handoff_until TIMESTAMP
            """
        )
        await conn.execute(
            """
            ALTER TABLE conversations
            ADD COLUMN IF NOT EXISTS clarification_attempts INTEGER NOT NULL DEFAULT 0
            """
        )
        await conn.execute(
            """
            ALTER TABLE conversations
            ADD COLUMN IF NOT EXISTS topic_change_count INTEGER NOT NULL DEFAULT 0
            """
        )
        await conn.execute(
            """
            ALTER TABLE conversations
            ADD COLUMN IF NOT EXISTS current_topic VARCHAR(160)
            """
        )
        await conn.execute(
            """
            ALTER TABLE conversations
            ADD COLUMN IF NOT EXISTS interaction_closed BOOLEAN NOT NULL DEFAULT FALSE
            """
        )

        _handoff_schema_ready = True


async def _create_conversation(conn, identity_id: UUID) -> UUID:
    return await conn.fetchval(
        """
        INSERT INTO conversations (contact_identity_id, status, started_at, last_message_at)
        VALUES ($1, $2, NOW(), NOW())
        RETURNING id
        """,
        identity_id,
        CONVERSATION_STATUS_OPEN,
    )


async def _create_draft_order(conn, conversation_id: UUID, contact_id: UUID) -> UUID:
    return await conn.fetchval(
        """
        INSERT INTO orders (conversation_id, contact_id, status, created_at, updated_at)
        VALUES ($1, $2, $3, NOW(), NOW())
        RETURNING id
        """,
        conversation_id,
        contact_id,
        ORDER_STATUS_DRAFT,
    )


async def _ensure_draft_order(conn, conversation_id: UUID, contact_id: UUID) -> UUID:
    existing = await conn.fetchval(
        """
        SELECT id
        FROM orders
        WHERE conversation_id = $1 AND status = ANY($2::text[])
        ORDER BY created_at DESC
        LIMIT 1
        """,
        conversation_id,
        list(ACTIVE_ORDER_STATUSES),
    )
    if existing:
        return existing
    return await _create_draft_order(conn, conversation_id, contact_id)


async def _build_session_response(
    conn,
    conversation_id: UUID,
    sender_id: str,
    plataforma: str,
    created: bool,
) -> SessionResponse:
    row = await conn.fetchrow(
        """
        SELECT
            conv.id,
            conv.status,
            conv.last_message_at,
            ci.contact_id,
            c.nombre,
            c.telefono AS contact_phone,
            ord.telefono AS order_phone,
            ord.cantidad,
            ord.ciudad,
            item.quantity AS item_quantity,
            item.unit AS item_unit
        FROM conversations conv
        JOIN contact_identities ci ON ci.id = conv.contact_identity_id
        JOIN contacts c ON c.id = ci.contact_id
        LEFT JOIN LATERAL (
            SELECT telefono, cantidad, ciudad
            FROM orders
            WHERE conversation_id = conv.id
              AND status = ANY($2::text[])
            ORDER BY created_at DESC
            LIMIT 1
        ) ord ON TRUE
        LEFT JOIN LATERAL (
            SELECT quantity, unit
            FROM order_items
            WHERE order_id = (
                SELECT id
                FROM orders
                WHERE conversation_id = conv.id
                  AND status = ANY($2::text[])
                ORDER BY created_at DESC
                LIMIT 1
            )
              AND product_code = $3
            LIMIT 1
        ) item ON TRUE
        WHERE conv.id = $1
        """,
        conversation_id,
        list(ACTIVE_ORDER_STATUSES),
        PRODUCT_CODE_NOPAL,
    )
    if not row:
        raise RuntimeError(
            ERROR_CONVERSATION_NOT_FOUND_TEMPLATE.format(conversation_id=conversation_id)
        )

    cantidad = row["cantidad"] or _format_item_quantity(row["item_quantity"], row["item_unit"])
    telefono = row["contact_phone"] or row["order_phone"]
    return SessionResponse(
        id=row["id"],
        contact_id=row["contact_id"],
        sender_id=sender_id,
        plataforma=plataforma,
        ultimo_contacto=row["last_message_at"],
        activa=row["status"] == CONVERSATION_STATUS_OPEN,
        telefono=telefono,
        cantidad=cantidad,
        ciudad=row["ciudad"],
        nombre=row["nombre"],
        handoff_state=HANDOFF_STATE_AUTO,
        created=created,
    )


async def get_or_create_session(sender_id: str, plataforma: str) -> SessionResponse:
    logger.info(LOG_GET_OR_CREATE_SESSION_TEMPLATE, sender_id, plataforma)
    async with get_connection() as conn:
        await _ensure_handoff_schema(conn)
        await _deactivate_expired(conn)

        row = await conn.fetchrow(
            """
            SELECT
                ci.id AS identity_id,
                ci.contact_id,
                conv.id AS conversation_id,
                conv.last_message_at,
                conv.started_at,
                conv.interaction_closed
            FROM contact_identities ci
            LEFT JOIN LATERAL (
                SELECT id, last_message_at, started_at, interaction_closed
                FROM conversations
                WHERE contact_identity_id = ci.id AND status = $3
                ORDER BY started_at DESC
                LIMIT 1
            ) conv ON TRUE
            WHERE ci.sender_id = $1 AND ci.plataforma = $2
            """,
            sender_id,
            plataforma,
            CONVERSATION_STATUS_OPEN,
        )

        if not row:
            contact_id = await conn.fetchval(
                """
                INSERT INTO contacts (created_at, updated_at)
                VALUES (NOW(), NOW())
                RETURNING id
                """
            )
            identity_id = await conn.fetchval(
                """
                INSERT INTO contact_identities (
                    contact_id, sender_id, plataforma, first_seen_at, last_seen_at
                )
                VALUES ($1, $2, $3, NOW(), NOW())
                RETURNING id
                """,
                contact_id,
                sender_id,
                plataforma,
            )
            conversation_id = await _create_conversation(conn, identity_id)
            await _create_draft_order(conn, conversation_id, contact_id)
            logger.info(LOG_NEW_CONVERSATION_TEMPLATE, conversation_id)
            return await _build_session_response(
                conn,
                conversation_id,
                sender_id,
                plataforma,
                created=True,
            )

        await conn.execute(
            "UPDATE contact_identities SET last_seen_at = NOW() WHERE id = $1",
            row["identity_id"],
        )

        conversation_id = row["conversation_id"]
        created = False

        if (
            not conversation_id
            or row["interaction_closed"]
            or _is_conversation_expired(row["last_message_at"], row["started_at"])
        ):
            if conversation_id:
                await _close_conversation(conn, conversation_id)
            conversation_id = await _create_conversation(conn, row["identity_id"])
            await _create_draft_order(conn, conversation_id, row["contact_id"])
            created = True
            logger.info(LOG_CONVERSATION_RENEWED_TEMPLATE, conversation_id)
        else:
            await conn.execute(
                "UPDATE conversations SET last_message_at = NOW() WHERE id = $1",
                conversation_id,
            )
            await _ensure_draft_order(conn, conversation_id, row["contact_id"])
            logger.info(LOG_ACTIVE_CONVERSATION_TEMPLATE, conversation_id)

        return await _build_session_response(
            conn,
            conversation_id,
            sender_id,
            plataforma,
            created=created,
        )


async def mark_human_intervention(sender_id: str, plataforma: str) -> Optional[UUID]:
    logger.info(LOG_HANDOFF_MARK_INTERVENTION_TEMPLATE, sender_id, plataforma)
    conversation_id, _, _ = await set_handoff_state(
        sender_id,
        plataforma,
        True,
        reason=HANDOFF_SOURCE_AUTO,
    )
    return conversation_id


async def set_handoff_state(
    sender_id: str,
    plataforma: str,
    locked: bool,
    *,
    reason: Optional[str] = None,
) -> tuple[Optional[UUID], str, Optional[datetime]]:
    async with get_connection() as conn:
        await _ensure_handoff_schema(conn)
        await _deactivate_expired(conn)

        row = await conn.fetchrow(
            """
            SELECT
                ci.id AS identity_id,
                ci.contact_id,
                conv.id AS conversation_id
            FROM contact_identities ci
            LEFT JOIN LATERAL (
                SELECT id
                FROM conversations
                WHERE contact_identity_id = ci.id AND status = $3
                ORDER BY started_at DESC
                LIMIT 1
            ) conv ON TRUE
            WHERE ci.sender_id = $1 AND ci.plataforma = $2
            """,
            sender_id,
            plataforma,
            CONVERSATION_STATUS_OPEN,
        )

        if not row:
            contact_id = await conn.fetchval(
                """
                INSERT INTO contacts (created_at, updated_at)
                VALUES (NOW(), NOW())
                RETURNING id
                """
            )
            identity_id = await conn.fetchval(
                """
                INSERT INTO contact_identities (
                    contact_id, sender_id, plataforma, first_seen_at, last_seen_at
                )
                VALUES ($1, $2, $3, NOW(), NOW())
                RETURNING id
                """,
                contact_id,
                sender_id,
                plataforma,
            )
            conversation_id = await _create_conversation(conn, identity_id)
            await _create_draft_order(conn, conversation_id, contact_id)
        else:
            conversation_id = row["conversation_id"]
            if not conversation_id:
                conversation_id = await _create_conversation(conn, row["identity_id"])
                await _create_draft_order(conn, conversation_id, row["contact_id"])

        if locked:
            handoff_until = await _lock_handoff(
                conn,
                conversation_id,
                source=reason or HANDOFF_SOURCE_MANUAL,
            )
            return conversation_id, HANDOFF_STATE_HUMAN_LOCKED, handoff_until

        await _unlock_handoff(conn, conversation_id)
        return conversation_id, HANDOFF_STATE_AUTO, None


async def is_bot_handoff_locked(session_id: UUID) -> bool:
    async with get_connection() as conn:
        await _ensure_handoff_schema(conn)
        row = await conn.fetchrow(
            """
            SELECT handoff_state, handoff_until
            FROM conversations
            WHERE id = $1
            """,
            session_id,
        )
        if not row:
            return False

        if row["handoff_state"] != HANDOFF_STATE_HUMAN_LOCKED:
            return False

        handoff_until = row["handoff_until"]
        if handoff_until and handoff_until <= datetime.utcnow():
            await _unlock_handoff(conn, session_id)
            logger.info(LOG_HANDOFF_AUTO_EXPIRED_TEMPLATE, session_id)
            return False

        return True


async def _lock_handoff(conn, conversation_id: UUID, *, source: str) -> Optional[datetime]:
    handoff_until = _handoff_until_from_now()
    await conn.execute(
        """
        UPDATE conversations
        SET handoff_state = $2,
            handoff_source = $3,
            handoff_locked_at = NOW(),
            handoff_until = $4,
            last_message_at = NOW()
        WHERE id = $1
        """,
        conversation_id,
        HANDOFF_STATE_HUMAN_LOCKED,
        source,
        handoff_until,
    )
    logger.info(LOG_HANDOFF_LOCK_TEMPLATE, conversation_id, source)
    return handoff_until


async def _unlock_handoff(conn, conversation_id: UUID) -> None:
    await conn.execute(
        """
        UPDATE conversations
        SET handoff_state = $2,
            handoff_source = NULL,
            handoff_locked_at = NULL,
            handoff_until = NULL,
            last_message_at = NOW()
        WHERE id = $1
        """,
        conversation_id,
        HANDOFF_STATE_AUTO,
    )
    logger.info(LOG_HANDOFF_UNLOCK_TEMPLATE, conversation_id)


async def merge_extracted_data(
    session_id: UUID,
    extracted: ExtractResponse,
    is_first_message: bool,
) -> ExtractResponse:
    """Merge extracted fields into the active order, allowing explicit user updates."""
    logger.info(LOG_MERGE_DATA_TEMPLATE, session_id)
    async with get_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                conv.id AS conversation_id,
                ci.contact_id,
                c.nombre AS contact_name,
                c.telefono AS contact_phone,
                ord.id AS order_id,
                ord.telefono AS order_phone,
                ord.cantidad,
                ord.ciudad,
                item.quantity AS item_quantity,
                item.unit AS item_unit
            FROM conversations conv
            JOIN contact_identities ci ON ci.id = conv.contact_identity_id
            JOIN contacts c ON c.id = ci.contact_id
            LEFT JOIN LATERAL (
                SELECT id, telefono, cantidad, ciudad
                FROM orders
                WHERE conversation_id = conv.id
                  AND status = ANY($2::text[])
                ORDER BY created_at DESC
                LIMIT 1
            ) ord ON TRUE
            LEFT JOIN LATERAL (
                SELECT quantity, unit
                FROM order_items
                WHERE order_id = ord.id AND product_code = $3
                LIMIT 1
            ) item ON TRUE
            WHERE conv.id = $1
            """,
            session_id,
            list(ACTIVE_ORDER_STATUSES),
            PRODUCT_CODE_NOPAL,
        )
        if not row:
            return extracted

        order_id = row["order_id"]
        if not order_id:
            order_id = await _create_draft_order(conn, session_id, row["contact_id"])

        existing_cantidad = row["cantidad"] or _format_item_quantity(row["item_quantity"], row["item_unit"])
        nombre = extracted.nombre or row["contact_name"]
        telefono = extracted.telefono or row["order_phone"] or row["contact_phone"]
        cantidad = extracted.cantidad or existing_cantidad
        ciudad = extracted.ciudad or row["ciudad"]

        if extracted.nombre and extracted.nombre != row["contact_name"]:
            await conn.execute(
                """
                UPDATE contacts
                SET nombre = $2, updated_at = NOW()
                WHERE id = $1
                """,
                row["contact_id"],
                extracted.nombre,
            )

        if extracted.telefono and extracted.telefono != row["contact_phone"]:
            await conn.execute(
                """
                UPDATE contacts
                SET telefono = $2, updated_at = NOW()
                WHERE id = $1
                """,
                row["contact_id"],
                extracted.telefono,
            )

        await conn.execute(
            """
            UPDATE orders
            SET telefono = COALESCE($2, telefono),
                cantidad = COALESCE($3, cantidad),
                ciudad = COALESCE($4, ciudad),
                updated_at = NOW()
            WHERE id = $1
            """,
            order_id,
            telefono,
            cantidad,
            ciudad,
        )

        await conn.execute(
            "UPDATE conversations SET last_message_at = NOW() WHERE id = $1",
            session_id,
        )

        await _upsert_primary_order_item(conn, order_id, cantidad)

        merged = ExtractResponse(
            nombre=nombre,
            telefono=telefono,
            cantidad=cantidad,
            ciudad=ciudad,
        )
        datos_en = DATOS_EXTRAIDOS_PRIMER_MENSAJE if is_first_message else DATOS_EXTRAIDOS_RESPUESTA
        logger.info(LOG_MERGED_SESSION_TEMPLATE, merged, datos_en)
        return merged


async def merge_contact_data(
    session_id: UUID,
    extracted: ExtractResponse,
) -> ExtractResponse:
    """Merge contact fields into contacts table without advancing order flow."""
    async with get_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                ci.contact_id,
                c.nombre AS contact_name,
                c.telefono AS contact_phone,
                ord.cantidad,
                ord.ciudad,
                item.quantity AS item_quantity,
                item.unit AS item_unit
            FROM conversations conv
            JOIN contact_identities ci ON ci.id = conv.contact_identity_id
            JOIN contacts c ON c.id = ci.contact_id
            LEFT JOIN LATERAL (
                SELECT cantidad, ciudad
                FROM orders
                WHERE conversation_id = conv.id
                  AND status = ANY($2::text[])
                ORDER BY created_at DESC
                LIMIT 1
            ) ord ON TRUE
            LEFT JOIN LATERAL (
                SELECT quantity, unit
                FROM order_items
                WHERE order_id = (
                    SELECT id
                    FROM orders
                    WHERE conversation_id = conv.id
                      AND status = ANY($2::text[])
                    ORDER BY created_at DESC
                    LIMIT 1
                )
                  AND product_code = $3
                LIMIT 1
            ) item ON TRUE
            WHERE conv.id = $1
            """,
            session_id,
            list(ACTIVE_ORDER_STATUSES),
            PRODUCT_CODE_NOPAL,
        )
        if not row:
            return extracted

        if extracted.nombre and extracted.nombre != row["contact_name"]:
            await conn.execute(
                """
                UPDATE contacts
                SET nombre = $2, updated_at = NOW()
                WHERE id = $1
                """,
                row["contact_id"],
                extracted.nombre,
            )

        if extracted.telefono and extracted.telefono != row["contact_phone"]:
            await conn.execute(
                """
                UPDATE contacts
                SET telefono = $2, updated_at = NOW()
                WHERE id = $1
                """,
                row["contact_id"],
                extracted.telefono,
            )

        await conn.execute(
            "UPDATE conversations SET last_message_at = NOW() WHERE id = $1",
            session_id,
        )

        return ExtractResponse(
            nombre=extracted.nombre or row["contact_name"],
            telefono=extracted.telefono or row["contact_phone"],
            cantidad=row["cantidad"] or _format_item_quantity(row["item_quantity"], row["item_unit"]),
            ciudad=extracted.ciudad or row["ciudad"],
        )


def get_missing_fields(data: ExtractResponse) -> list[str]:
    missing = []
    if not data.nombre:
        missing.append(FIELD_NOMBRE)
    if not data.cantidad:
        missing.append(FIELD_CANTIDAD)
    if not data.ciudad:
        missing.append(FIELD_CIUDAD)
    if not data.telefono:
        missing.append(FIELD_TELEFONO)
    return missing


def get_missing_contact_fields(data: ExtractResponse) -> list[str]:
    missing = []
    if not data.nombre:
        missing.append(FIELD_NOMBRE)
    if not data.telefono:
        missing.append(FIELD_TELEFONO)
    return missing


async def is_repeat_greeting(session_id: UUID) -> bool:
    async with get_connection() as conn:
        total = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM messages
            WHERE conversation_id = $1
                            AND direction = $2
                            AND categoria = $3
            """,
            session_id,
                        DIRECTION_INBOUND,
                        CATEGORY_SALUDO,
        )
        return bool(total and total > 1)


async def get_active_order_status(session_id: UUID) -> Optional[str]:
    async with get_connection() as conn:
        return await conn.fetchval(
            """
            SELECT status
            FROM orders
            WHERE conversation_id = $1
                            AND status = ANY($2::text[])
            ORDER BY created_at DESC
            LIMIT 1
            """,
            session_id,
                        list(ACTIVE_ORDER_STATUSES),
        )


async def get_session_data(session_id: UUID) -> ExtractResponse:
    async with get_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                c.nombre,
                c.telefono AS contact_phone,
                ord.telefono AS order_phone,
                ord.cantidad,
                ord.ciudad,
                item.quantity AS item_quantity,
                item.unit AS item_unit
            FROM conversations conv
            JOIN contact_identities ci ON ci.id = conv.contact_identity_id
            JOIN contacts c ON c.id = ci.contact_id
            LEFT JOIN LATERAL (
                SELECT telefono, cantidad, ciudad
                FROM orders
                WHERE conversation_id = conv.id
                  AND status = ANY($2::text[])
                ORDER BY created_at DESC
                LIMIT 1
            ) ord ON TRUE
            LEFT JOIN LATERAL (
                SELECT quantity, unit
                FROM order_items
                WHERE order_id = (
                    SELECT id
                    FROM orders
                                        WHERE conversation_id = conv.id
                                            AND status = ANY($2::text[])
                    ORDER BY created_at DESC
                    LIMIT 1
                )
                                    AND product_code = $3
                LIMIT 1
            ) item ON TRUE
            WHERE conv.id = $1
            """,
            session_id,
                        list(ACTIVE_ORDER_STATUSES),
                        PRODUCT_CODE_NOPAL,
        )
        if not row:
            return ExtractResponse()

        cantidad = row["cantidad"] or _format_item_quantity(row["item_quantity"], row["item_unit"])
        return ExtractResponse(
            nombre=row["nombre"],
            telefono=row["contact_phone"] or row["order_phone"],
            cantidad=cantidad,
            ciudad=row["ciudad"],
        )


async def upsert_order_for_lead(
    session_id: UUID,
    plataforma: str,
    tipo: str,
    nombre: Optional[str],
    telefono: Optional[str],
    cantidad: Optional[str],
    ciudad: Optional[str],
    datos_extraidos_en: str,
) -> Optional[UUID]:
    if tipo != CATEGORY_LEAD:
        return None

    async with get_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                ci.contact_id,
                conv.id AS conversation_id,
                c.nombre AS contact_name,
                c.telefono AS contact_phone,
                ord.id AS order_id,
                ord.telefono AS order_phone,
                ord.cantidad,
                ord.ciudad,
                item.quantity AS item_quantity,
                item.unit AS item_unit
            FROM conversations conv
            JOIN contact_identities ci ON ci.id = conv.contact_identity_id
            JOIN contacts c ON c.id = ci.contact_id
            LEFT JOIN LATERAL (
                SELECT id, telefono, cantidad, ciudad
                FROM orders
                WHERE conversation_id = conv.id
                                    AND status = ANY($2::text[])
                ORDER BY created_at DESC
                LIMIT 1
            ) ord ON TRUE
            LEFT JOIN LATERAL (
                SELECT quantity, unit
                FROM order_items
                                WHERE order_id = ord.id AND product_code = $3
                LIMIT 1
            ) item ON TRUE
            WHERE conv.id = $1
            """,
            session_id,
                        list(ACTIVE_ORDER_STATUSES),
                        PRODUCT_CODE_NOPAL,
        )
        if not row:
            return None

        order_id = row["order_id"] or await _ensure_draft_order(
            conn,
            row["conversation_id"],
            row["contact_id"],
        )

        resolved_nombre = nombre or row["contact_name"]
        resolved_telefono = telefono or row["order_phone"] or row["contact_phone"]
        resolved_cantidad = (
            cantidad
            or row["cantidad"]
            or _format_item_quantity(row["item_quantity"], row["item_unit"])
        )
        resolved_ciudad = ciudad or row["ciudad"]
        missing = get_missing_fields(
            ExtractResponse(
                nombre=resolved_nombre,
                telefono=resolved_telefono,
                cantidad=resolved_cantidad,
                ciudad=resolved_ciudad,
            )
        )
        order_status = ORDER_STATUS_READY_TO_CONFIRM if not missing else ORDER_STATUS_COLLECTING

        if nombre:
            await conn.execute(
                """
                UPDATE contacts
                SET nombre = $2, updated_at = NOW()
                WHERE id = $1
                """,
                row["contact_id"],
                nombre,
            )

        if telefono:
            await conn.execute(
                """
                UPDATE contacts
                SET telefono = $2, updated_at = NOW()
                WHERE id = $1
                """,
                row["contact_id"],
                telefono,
            )

        await conn.execute(
            """
            UPDATE orders
            SET status = $2,
                intento = $3,
                telefono = COALESCE($4, telefono),
                cantidad = COALESCE($5, cantidad),
                ciudad = COALESCE($6, ciudad),
                notas = COALESCE(notas, $7),
                updated_at = NOW()
            WHERE id = $1
            """,
            order_id,
            order_status,
            tipo,
            resolved_telefono,
            resolved_cantidad,
            resolved_ciudad,
            ORDER_CAPTURE_NOTES_TEMPLATE.format(source=datos_extraidos_en, platform=plataforma),
        )

        await _upsert_primary_order_item(conn, order_id, resolved_cantidad)
        return order_id


async def apply_customer_order_intent(
    session_id: UUID,
    text: str,
    forced_intent: Optional[str] = None,
    confirmed_by_openai: bool = False,
) -> tuple[Optional[UUID], Optional[str]]:
    normalized_text = _normalize_text(text)

    async with get_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, status
            FROM orders
            WHERE conversation_id = $1
              AND status = ANY($2::text[])
            ORDER BY created_at DESC
            LIMIT 1
            """,
            session_id,
            list(ACTIVE_ORDER_STATUSES),
        )
        if not row:
            return None, None

        order_id = row["id"]
        intent = forced_intent

        if intent not in {ORDER_INTENT_CONFIRMED, ORDER_INTENT_CANCELLED}:
            intent = None

        if not intent and _contains_intent_phrase(normalized_text, ORDER_CANCELLATION_KEYWORDS):
            intent = ORDER_INTENT_CANCELLED

        if not intent and row["status"] == ORDER_STATUS_READY_TO_CONFIRM and _contains_intent_phrase(
            normalized_text,
            ORDER_CONFIRMATION_KEYWORDS,
        ):
            intent = ORDER_INTENT_CONFIRMED

        if intent == ORDER_INTENT_CANCELLED:
            await conn.execute(
                """
                UPDATE orders
                SET status = $2, closed_at = NOW(), updated_at = NOW()
                WHERE id = $1
                """,
                order_id,
                ORDER_STATUS_CANCELLED,
            )
            await _close_conversation(conn, session_id)
            return order_id, ORDER_INTENT_CANCELLED

        if intent == ORDER_INTENT_CONFIRMED and row["status"] == ORDER_STATUS_READY_TO_CONFIRM:
            await conn.execute(
                """
                UPDATE orders
                SET status = $3,
                    confirmed_by_openai = COALESCE(confirmed_by_openai, false) OR $2,
                    closed_at = NOW(),
                    updated_at = NOW()
                WHERE id = $1
                """,
                order_id,
                confirmed_by_openai,
                ORDER_STATUS_CONFIRMED,
            )
            await _close_conversation(conn, session_id)
            return order_id, ORDER_INTENT_CONFIRMED

        return order_id, None


async def get_greeting_context(session_id: UUID) -> tuple[Optional[str], Optional[str], Optional[str]]:
    async with get_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                c.nombre,
                hist.cantidad AS last_cantidad,
                hist.ciudad AS last_ciudad,
                hist.item_quantity AS last_item_quantity,
                hist.item_unit AS last_item_unit
            FROM conversations conv
            JOIN contact_identities ci ON ci.id = conv.contact_identity_id
            JOIN contacts c ON c.id = ci.contact_id
            LEFT JOIN LATERAL (
                SELECT
                    ord.cantidad,
                    ord.ciudad,
                    item.quantity AS item_quantity,
                    item.unit AS item_unit
                FROM orders ord
                LEFT JOIN LATERAL (
                    SELECT quantity, unit
                    FROM order_items
                    WHERE order_id = ord.id
                                            AND product_code = $2
                    LIMIT 1
                ) item ON TRUE
                WHERE ord.contact_id = ci.contact_id
                                    AND ord.status = ANY($3::text[])
                ORDER BY COALESCE(ord.closed_at, ord.updated_at, ord.created_at) DESC
                LIMIT 1
            ) hist ON TRUE
            WHERE conv.id = $1
            """,
            session_id,
                        PRODUCT_CODE_NOPAL,
                        [
                                ORDER_STATUS_READY_TO_CONFIRM,
                                ORDER_STATUS_CONFIRMED,
                                ORDER_STATUS_CLOSED,
                        ],
        )
        if not row:
            return None, None, None

        cantidad = row["last_cantidad"] or _format_item_quantity(
            row["last_item_quantity"],
            row["last_item_unit"],
        )
        return row["nombre"], cantidad, row["last_ciudad"]


async def save_message(
    session_id: UUID,
    plataforma: str,
    sender_id: str,
    direction: str,
    texto: str,
    categoria: Optional[str] = None,
    intent_category: Optional[str] = None,
    detected_topic: Optional[str] = None,
    external_message_id: Optional[str] = None,
    order_id: Optional[UUID] = None,
    payload: Optional[dict] = None,
    classified_by_openai: bool = False,
) -> UUID:
    payload_json = json.dumps(payload) if payload else None
    normalized_topic = (detected_topic or "").strip()[:160] or None

    async with get_connection() as conn:
        message_id = await conn.fetchval(
            """
            INSERT INTO messages (
                conversation_id,
                order_id,
                external_message_id,
                direction,
                plataforma,
                sender_id,
                categoria,
                intent_category,
                detected_topic,
                texto,
                classified_by_openai,
                payload,
                created_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, NOW())
            RETURNING id
            """,
            session_id,
            order_id,
            external_message_id,
            direction,
            plataforma,
            sender_id,
            categoria,
            intent_category,
            normalized_topic,
            texto,
            classified_by_openai,
            payload_json,
        )

        await conn.execute(
            "UPDATE conversations SET last_message_at = NOW() WHERE id = $1",
            session_id,
        )

        logger.info(LOG_MESSAGE_SAVED_TEMPLATE, message_id, direction, categoria)
        return message_id


async def get_conversation_flow_state(session_id: UUID) -> ConversationFlowState:
    async with get_connection() as conn:
        await _ensure_handoff_schema(conn)
        row = await conn.fetchrow(
            """
            SELECT clarification_attempts, topic_change_count, current_topic, interaction_closed
            FROM conversations
            WHERE id = $1
            """,
            session_id,
        )
        if not row:
            return ConversationFlowState()
        return ConversationFlowState(
            clarification_attempts=row["clarification_attempts"] or 0,
            topic_change_count=row["topic_change_count"] or 0,
            current_topic=row["current_topic"],
            interaction_closed=bool(row["interaction_closed"]),
        )


async def increment_clarification_attempt(session_id: UUID) -> int:
    async with get_connection() as conn:
        await _ensure_handoff_schema(conn)
        return await conn.fetchval(
            """
            UPDATE conversations
            SET clarification_attempts = clarification_attempts + 1,
                last_message_at = NOW()
            WHERE id = $1
            RETURNING clarification_attempts
            """,
            session_id,
        )


async def reset_clarification_attempts(session_id: UUID) -> None:
    async with get_connection() as conn:
        await _ensure_handoff_schema(conn)
        await conn.execute(
            """
            UPDATE conversations
            SET clarification_attempts = 0,
                last_message_at = NOW()
            WHERE id = $1
            """,
            session_id,
        )


async def track_topic_change(session_id: UUID, topic: Optional[str]) -> tuple[int, bool]:
    normalized = _normalize_topic_value(topic)

    async with get_connection() as conn:
        await _ensure_handoff_schema(conn)
        row = await conn.fetchrow(
            """
            SELECT current_topic, topic_change_count
            FROM conversations
            WHERE id = $1
            """,
            session_id,
        )
        if not row:
            return 0, False

        previous_topic = _normalize_topic_value(row["current_topic"])
        current_count = row["topic_change_count"] or 0

        if not normalized or _is_generic_topic(normalized):
            return current_count, False

        if not previous_topic or _is_generic_topic(previous_topic):
            await conn.execute(
                """
                UPDATE conversations
                SET current_topic = $2,
                    last_message_at = NOW()
                WHERE id = $1
                """,
                session_id,
                normalized,
            )
            return current_count, False

        if normalized == previous_topic:
            return current_count, False

        new_count = current_count + 1
        await conn.execute(
            """
            UPDATE conversations
            SET current_topic = $2,
                topic_change_count = $3,
                last_message_at = NOW()
            WHERE id = $1
            """,
            session_id,
            normalized,
            new_count,
        )

        return new_count, new_count > settings.max_topic_changes


async def close_interaction(session_id: UUID) -> None:
    async with get_connection() as conn:
        await _ensure_handoff_schema(conn)
        await conn.execute(
            """
            UPDATE conversations
            SET interaction_closed = TRUE,
                clarification_attempts = 0,
                last_message_at = NOW()
            WHERE id = $1
            """,
            session_id,
        )
