import logging
from typing import Optional
from uuid import UUID

import httpx

from app.config import settings
from app.models.lead import ExtractResponse, RespondResponse
from app.services.session_manager import get_missing_fields, get_session_data

logger = logging.getLogger(__name__)

GRAPH_BASE = f"https://graph.facebook.com/{settings.meta_graph_api_version}"


def build_response_message(data: ExtractResponse) -> tuple[str, list[str]]:
    missing = get_missing_fields(data)
    if not missing:
        return "¡Listo! En un momento te contactamos.", []

    if "cantidad" in missing:
        return (
            "¡Claro! ¿Cuántos kilos de nopal necesitas aproximadamente?",
            missing,
        )
    if "ciudad" in missing:
        return "Perfecto, ¿de qué ciudad nos escribes?", missing
    if "telefono" in missing:
        return (
            "Genial, ¿me compartes un número de contacto para darte seguimiento?",
            missing,
        )
    return "¡Listo! En un momento te contactamos.", []


async def send_meta_message(
    sender_id: str,
    plataforma: str,
    text: str,
) -> bool:
    logger.info("Sending message to %s via %s", sender_id, plataforma)
    if not settings.meta_page_access_token:
        logger.warning("META_PAGE_ACCESS_TOKEN not set, skipping send")
        return False

    try:
        if plataforma == "whatsapp":
            return await _send_whatsapp(sender_id, text)
        return await _send_messenger(sender_id, text)
    except httpx.HTTPError as exc:
        logger.error("Failed to send Meta message: %s", exc)
        return False


async def _send_whatsapp(recipient_id: str, text: str) -> bool:
    url = f"{GRAPH_BASE}/{settings.whatsapp_phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient_id,
        "type": "text",
        "text": {"body": text},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            url,
            json=payload,
            params={"access_token": settings.meta_page_access_token},
        )
        response.raise_for_status()
        logger.info("WhatsApp message sent to %s", recipient_id)
        return True


async def _send_messenger(recipient_id: str, text: str) -> bool:
    url = f"{GRAPH_BASE}/me/messages"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            url,
            json=payload,
            params={"access_token": settings.meta_page_access_token},
        )
        response.raise_for_status()
        logger.info("Messenger message sent to %s", recipient_id)
        return True


async def generate_and_send_response(
    sender_id: str,
    plataforma: str,
    session_id: Optional[UUID] = None,
    texto_override: Optional[str] = None,
) -> RespondResponse:
    if texto_override:
        mensaje = texto_override
        campos_faltantes: list[str] = []
    elif session_id:
        data = await get_session_data(session_id)
        mensaje, campos_faltantes = build_response_message(data)
    else:
        mensaje = "¡Hola! ¿En qué te podemos ayudar con nopal?"
        campos_faltantes = ["cantidad", "ciudad", "telefono"]

    enviado = await send_meta_message(sender_id, plataforma, mensaje)
    logger.info("Response generated: '%s' (sent=%s)", mensaje[:80], enviado)
    return RespondResponse(
        mensaje=mensaje,
        enviado=enviado,
        campos_faltantes=campos_faltantes,
    )
