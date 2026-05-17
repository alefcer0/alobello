import logging
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from app.config import settings
from app.db.connection import get_connection
from app.models.lead import ExtractResponse
from app.models.session import SessionResponse
from app.services.extractor import extract_data

logger = logging.getLogger(__name__)


def _expiry_cutoff() -> datetime:
    return datetime.utcnow() - timedelta(hours=settings.session_expiry_hours)


async def _deactivate_expired(conn) -> None:
    cutoff = _expiry_cutoff()
    await conn.execute(
        "UPDATE sessions SET activa = FALSE WHERE activa = TRUE AND ultimo_contacto < $1",
        cutoff,
    )


async def get_or_create_session(sender_id: str, plataforma: str) -> SessionResponse:
    logger.info("Getting or creating session for sender=%s platform=%s", sender_id, plataforma)
    cutoff = _expiry_cutoff()
    async with get_connection() as conn:
        await _deactivate_expired(conn)

        row = await conn.fetchrow(
            """
            SELECT id, sender_id, plataforma, ultimo_contacto, activa,
                   telefono, cantidad, ciudad, nombre
            FROM sessions
            WHERE sender_id = $1 AND plataforma = $2
            """,
            sender_id,
            plataforma,
        )

        if row and row["activa"] and row["ultimo_contacto"] >= cutoff:
            await conn.execute(
                "UPDATE sessions SET ultimo_contacto = NOW() WHERE id = $1",
                row["id"],
            )
            logger.info("Active session found: %s", row["id"])
            return SessionResponse(
                id=row["id"],
                sender_id=row["sender_id"],
                plataforma=row["plataforma"],
                ultimo_contacto=row["ultimo_contacto"],
                activa=True,
                telefono=row["telefono"],
                cantidad=row["cantidad"],
                ciudad=row["ciudad"],
                nombre=row["nombre"],
                created=False,
            )

        if row:
            renewed = await conn.fetchrow(
                """
                UPDATE sessions
                SET activa = TRUE, ultimo_contacto = NOW(),
                    telefono = NULL, cantidad = NULL, ciudad = NULL, nombre = NULL
                WHERE id = $1
                RETURNING id, sender_id, plataforma, ultimo_contacto, activa,
                          telefono, cantidad, ciudad, nombre
                """,
                row["id"],
            )
            logger.info("Session renewed after expiry: %s", row["id"])
            return SessionResponse(
                id=renewed["id"],
                sender_id=renewed["sender_id"],
                plataforma=renewed["plataforma"],
                ultimo_contacto=renewed["ultimo_contacto"],
                activa=renewed["activa"],
                telefono=renewed["telefono"],
                cantidad=renewed["cantidad"],
                ciudad=renewed["ciudad"],
                nombre=renewed["nombre"],
                created=True,
            )

        new_row = await conn.fetchrow(
            """
            INSERT INTO sessions (sender_id, plataforma, ultimo_contacto, activa)
            VALUES ($1, $2, NOW(), TRUE)
            RETURNING id, sender_id, plataforma, ultimo_contacto, activa,
                      telefono, cantidad, ciudad, nombre
            """,
            sender_id,
            plataforma,
        )
        logger.info("New session created: %s", new_row["id"])
        return SessionResponse(
            id=new_row["id"],
            sender_id=new_row["sender_id"],
            plataforma=new_row["plataforma"],
            ultimo_contacto=new_row["ultimo_contacto"],
            activa=new_row["activa"],
            telefono=new_row["telefono"],
            cantidad=new_row["cantidad"],
            ciudad=new_row["ciudad"],
            nombre=new_row["nombre"],
            created=True,
        )


async def merge_extracted_data(
    session_id: UUID,
    extracted: ExtractResponse,
    is_first_message: bool,
) -> ExtractResponse:
    """Accumulate extracted fields without overwriting existing session values."""
    logger.info("Merging extracted data into session %s", session_id)
    async with get_connection() as conn:
        row = await conn.fetchrow(
            "SELECT telefono, cantidad, ciudad, nombre FROM sessions WHERE id = $1",
            session_id,
        )
        if not row:
            return extracted

        telefono = row["telefono"] or extracted.telefono
        cantidad = row["cantidad"] or extracted.cantidad
        ciudad = row["ciudad"] or extracted.ciudad

        await conn.execute(
            """
            UPDATE sessions
            SET telefono = $2, cantidad = $3, ciudad = $4, ultimo_contacto = NOW()
            WHERE id = $1
            """,
            session_id,
            telefono,
            cantidad,
            ciudad,
        )

        merged = ExtractResponse(telefono=telefono, cantidad=cantidad, ciudad=ciudad)
        datos_en = "primer_mensaje" if is_first_message else "respuesta"
        logger.info("Merged session data: %s (source=%s)", merged, datos_en)
        return merged


def get_missing_fields(data: ExtractResponse) -> list[str]:
    missing = []
    if not data.cantidad:
        missing.append("cantidad")
    if not data.ciudad:
        missing.append("ciudad")
    if not data.telefono:
        missing.append("telefono")
    return missing


async def get_session_data(session_id: UUID) -> ExtractResponse:
    async with get_connection() as conn:
        row = await conn.fetchrow(
            "SELECT telefono, cantidad, ciudad FROM sessions WHERE id = $1",
            session_id,
        )
        if not row:
            return ExtractResponse()
        return ExtractResponse(
            telefono=row["telefono"],
            cantidad=row["cantidad"],
            ciudad=row["ciudad"],
        )


async def save_lead(
    session_id: UUID,
    plataforma: str,
    tipo: str,
    mensaje: str,
    nombre: Optional[str],
    telefono: Optional[str],
    cantidad: Optional[str],
    ciudad: Optional[str],
    datos_extraidos_en: str,
) -> UUID:
    async with get_connection() as conn:
        lead_id = await conn.fetchval(
            """
            INSERT INTO leads (
                session_id, plataforma, tipo, mensaje, nombre,
                telefono, cantidad, ciudad, datos_extraidos_en
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id
            """,
            session_id,
            plataforma,
            tipo,
            mensaje,
            nombre,
            telefono,
            cantidad,
            ciudad,
            datos_extraidos_en,
        )
        logger.info("Lead saved: %s (tipo=%s)", lead_id, tipo)
        return lead_id
