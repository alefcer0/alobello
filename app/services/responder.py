import logging
from typing import Optional
from uuid import UUID

import httpx
from openai import APIError, AsyncOpenAI, RateLimitError

from app.config import settings
from app.constants.common import PLATFORM_WHATSAPP
from app.constants.meta_api import (
    GRAPH_BASE_TEMPLATE,
    GRAPH_MESSENGER_ME_PATH,
    GRAPH_MESSAGES_PATH,
    HTTP_TIMEOUT_SECONDS,
    LOG_MESSENGER_SENT_TEMPLATE,
    LOG_RESPONSE_GENERATED_TEMPLATE,
    LOG_SEND_FAILED_TEMPLATE,
    LOG_SEND_MESSAGE_TEMPLATE,
    LOG_SKIP_SEND_MISSING_TOKEN,
    LOG_WHATSAPP_SENT_TEMPLATE,
    META_RESPONSE_ID_KEY,
    META_RESPONSE_MESSAGE_ID_KEY,
    META_RESPONSE_MESSAGES_KEY,
)
from app.constants.responses import (
    CLARIFICATION_FALLBACK_MESSAGES,
    CLARIFICATION_MAX_WORDS,
    CONTACT_MISSING_FIELD_LABELS,
    DEFAULT_MISSING_FIELDS,
    DEFAULT_CONTACT_FIELDS,
    FALLBACK_HELLO_RESPONSE,
    GENERAL_CONTACT_PROMPT_PLURAL_TEMPLATE,
    GENERAL_CONTACT_PROMPT_SINGLE_TEMPLATE,
    GENERAL_CONTACT_READY_TEMPLATE,
    GENERAL_TOPIC_ACK_TEMPLATE,
    GENERAL_TOPIC_FALLBACK,
    GREETING_RESPONSE,
    GREETING_WITH_HISTORY_NO_NAME_TEMPLATE,
    GREETING_WITH_HISTORY_TEMPLATE,
    GREETING_WITH_NAME_TEMPLATE,
    HUMAN_CONTACT_BRIDGE_RESPONSE,
    HUMAN_CONTACT_CONFIRMED_RESPONSE,
    HUMAN_JOIN_AND,
    HUMAN_JOIN_SEPARATOR,
    INTERACTION_CLOSE_RESPONSE,
    MISSING_FIELD_LABELS,
    MISSING_FIELDS_PROMPT_PLURAL_TEMPLATE,
    MISSING_FIELDS_PROMPT_SINGLE_TEMPLATE,
    ORDER_CANCELLED_RESPONSE,
    ORDER_CHANGE_DEFAULT_TEMPLATE,
    ORDER_CHANGE_WITH_SUMMARY_TEMPLATE,
    ORDER_CONFIRMED_RESPONSE,
    ORDER_CONFIRMATION_PROMPT_DEFAULT,
    ORDER_CONFIRMATION_PROMPT_WITH_SUMMARY,
    READY_RESPONSE,
    REPEATED_GREETING_RESPONSE,
    TECHNOLOGY_NEUTRAL_RESPONSE,
    TOPIC_CHANGE_SIMPLIFIED_RESPONSE,
)
from app.constants.webhook import MESSAGE_TYPE_TEXT, MESSAGING_PRODUCT_WHATSAPP
from app.models.lead import ExtractResponse, RespondResponse
from app.services.session_manager import (
    get_greeting_context,
    get_missing_contact_fields,
    get_missing_fields,
    get_session_data,
)
from app.services.bot_outbound_tracker import bot_outbound_tracker

logger = logging.getLogger(__name__)

GRAPH_BASE = GRAPH_BASE_TEMPLATE.format(version=settings.meta_graph_api_version)
_openai_client: AsyncOpenAI | None = None


def _openai_configured() -> bool:
    key = (settings.openai_api_key or "").strip()
    return bool(key) and key.startswith("sk-")


def _get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client


async def build_greeting_message(conversation_id: Optional[UUID] = None) -> str:
    if not conversation_id:
        return GREETING_RESPONSE

    nombre, cantidad, ciudad = await get_greeting_context(conversation_id)
    if nombre and cantidad and ciudad:
        return GREETING_WITH_HISTORY_TEMPLATE.format(name=nombre, quantity=cantidad, city=ciudad)
    if nombre:
        return GREETING_WITH_NAME_TEMPLATE.format(name=nombre)
    if cantidad and ciudad:
        return GREETING_WITH_HISTORY_NO_NAME_TEMPLATE.format(quantity=cantidad, city=ciudad)
    return GREETING_RESPONSE


def build_confirmation_message() -> str:
    return ORDER_CONFIRMED_RESPONSE


def build_cancellation_message() -> str:
    return ORDER_CANCELLED_RESPONSE


def build_repeated_greeting_message() -> str:
    return REPEATED_GREETING_RESPONSE


def build_human_contact_bridge_message() -> str:
    return HUMAN_CONTACT_BRIDGE_RESPONSE


def build_human_contact_confirmed_message() -> str:
    return HUMAN_CONTACT_CONFIRMED_RESPONSE


def build_technology_neutral_message() -> str:
    return TECHNOLOGY_NEUTRAL_RESPONSE


def build_topic_change_simplified_message() -> str:
    return TOPIC_CHANGE_SIMPLIFIED_RESPONSE


def _human_join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]}{HUMAN_JOIN_AND}{items[1]}"
    return HUMAN_JOIN_SEPARATOR.join(items[:-1]) + f"{HUMAN_JOIN_AND}{items[-1]}"


def build_response_message(data: ExtractResponse) -> tuple[str, list[str]]:
    missing = get_missing_fields(data)
    if not missing:
        return READY_RESPONSE, []

    pending = [MISSING_FIELD_LABELS.get(field, field) for field in missing]
    pending_text = _human_join(pending)
    if len(missing) == 1:
        return MISSING_FIELDS_PROMPT_SINGLE_TEMPLATE.format(pending=pending_text), missing
    return MISSING_FIELDS_PROMPT_PLURAL_TEMPLATE.format(pending=pending_text), missing


def _build_order_summary_bits(data: ExtractResponse) -> list[str]:
    bits: list[str] = []
    if data.cantidad:
        bits.append(data.cantidad)
    if data.ciudad:
        bits.append(f"para {data.ciudad}")
    if data.telefono:
        bits.append(f"al teléfono {data.telefono}")
    return bits


def build_order_confirmation_prompt(data: ExtractResponse) -> str:
    summary = _human_join(_build_order_summary_bits(data))
    if summary:
        return ORDER_CONFIRMATION_PROMPT_WITH_SUMMARY.format(summary=summary)
    return ORDER_CONFIRMATION_PROMPT_DEFAULT


def build_order_change_message(changed_fields: list[str], data: ExtractResponse) -> str:
    changes = _human_join(changed_fields)
    summary = _human_join(_build_order_summary_bits(data))
    if summary:
        return ORDER_CHANGE_WITH_SUMMARY_TEMPLATE.format(changes=changes, summary=summary)
    return ORDER_CHANGE_DEFAULT_TEMPLATE.format(changes=changes)


def _sanitize_topic(topic: Optional[str]) -> str:
    cleaned = (topic or "").strip(" .,:;!?")
    if not cleaned:
        return GENERAL_TOPIC_FALLBACK
    return cleaned


def build_interaction_close_message() -> str:
    return INTERACTION_CLOSE_RESPONSE


def _ensure_alobot_intro(message: str) -> str:
    clean = " ".join((message or "").split())
    if not clean:
        return "Soy Alobot 🌵🤖."
    if "alobot" in clean.lower():
        return clean
    return f"Soy Alobot 🌵🤖. {clean}"


def _fallback_clarification_message(attempt: int, missing_fields: list[str]) -> str:
    base = CLARIFICATION_FALLBACK_MESSAGES.get(
        min(max(attempt, 1), 3),
        CLARIFICATION_FALLBACK_MESSAGES[3],
    )
    if not missing_fields:
        return _ensure_alobot_intro(base) if attempt == 1 else base

    pending = [CONTACT_MISSING_FIELD_LABELS.get(field, field) for field in missing_fields]
    pending_text = _human_join(pending)
    msg = f"{base} Si puedes, también compárteme {pending_text}."
    return _ensure_alobot_intro(msg) if attempt == 1 else msg


async def build_clarification_message(
    *,
    user_text: str,
    topic_hint: Optional[str],
    missing_fields: list[str],
    attempt: int,
) -> tuple[str, bool]:
    fallback = _fallback_clarification_message(attempt, missing_fields)
    if not _openai_configured():
        return fallback, False

    topic_text = _sanitize_topic(topic_hint)
    pending = [CONTACT_MISSING_FIELD_LABELS.get(field, field) for field in missing_fields]
    pending_text = _human_join(pending) if pending else "ninguno"

    prompt = (
        "Redacta un solo mensaje breve y amable en español mexicano para aclarar la intención de un cliente. "
        f"Máximo {CLARIFICATION_MAX_WORDS} palabras. "
        "Debe hacer una pregunta clara para entender qué necesita y, si faltan datos, pedirlos sin sonar robótico. "
        "Si es el primer intento, incluye presentación explícita con la frase 'Soy Alobot 🌵🤖.'. "
        "Si es un intento posterior, no vuelvas a presentarte. "
        "No uses listas ni comillas ni markdown. "
        "Mantén tono humano del asistente Alobot.\n"
        f"Intento de aclaración: {attempt} de 3.\n"
        f"Tema detectado actual: {topic_text}.\n"
        f"Datos de contacto faltantes: {pending_text}.\n"
        f"Mensaje del usuario: {user_text}"
    )

    try:
        client = _get_openai_client()
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
            temperature=0.5,
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            return fallback, False
        final_text = _ensure_alobot_intro(text) if attempt == 1 else " ".join(text.split())
        return final_text, True
    except (RateLimitError, APIError, Exception):
        return fallback, False


def build_general_topic_response(topic: Optional[str], data: ExtractResponse) -> tuple[str, list[str]]:
    missing = get_missing_contact_fields(data)
    topic_text = _sanitize_topic(topic)
    intro = GENERAL_TOPIC_ACK_TEMPLATE.format(topic=topic_text)

    if not missing:
        return GENERAL_CONTACT_READY_TEMPLATE.format(topic=topic_text), []

    pending = [CONTACT_MISSING_FIELD_LABELS.get(field, field) for field in missing]
    pending_text = _human_join(pending)
    if len(missing) == 1:
        return (
            f"{intro} {GENERAL_CONTACT_PROMPT_SINGLE_TEMPLATE.format(pending=pending_text)}",
            missing,
        )

    return (
        f"{intro} {GENERAL_CONTACT_PROMPT_PLURAL_TEMPLATE.format(pending=pending_text)}",
        missing,
    )


async def send_meta_message(
    sender_id: str,
    plataforma: str,
    text: str,
) -> bool:
    logger.info(LOG_SEND_MESSAGE_TEMPLATE, sender_id, plataforma)
    if not settings.meta_page_access_token:
        logger.warning(LOG_SKIP_SEND_MISSING_TOKEN)
        return False

    try:
        if plataforma == PLATFORM_WHATSAPP:
            return await _send_whatsapp(sender_id, text)
        return await _send_messenger(sender_id, text)
    except httpx.HTTPError as exc:
        logger.error(LOG_SEND_FAILED_TEMPLATE, exc)
        return False


async def _send_whatsapp(recipient_id: str, text: str) -> bool:
    url = f"{GRAPH_BASE}/{settings.whatsapp_phone_number_id}{GRAPH_MESSAGES_PATH}"
    payload = {
        "messaging_product": MESSAGING_PRODUCT_WHATSAPP,
        "to": recipient_id,
        "type": MESSAGE_TYPE_TEXT,
        "text": {"body": text},
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        response = await client.post(
            url,
            json=payload,
            params={"access_token": settings.meta_page_access_token},
        )
        response.raise_for_status()
        body = response.json()
        outbound_messages = body.get(META_RESPONSE_MESSAGES_KEY, [])
        if outbound_messages:
            outbound_event_id = outbound_messages[0].get(META_RESPONSE_ID_KEY, "")
            await bot_outbound_tracker.track(outbound_event_id)
        logger.info(LOG_WHATSAPP_SENT_TEMPLATE, recipient_id)
        return True


async def _send_messenger(recipient_id: str, text: str) -> bool:
    url = f"{GRAPH_BASE}{GRAPH_MESSENGER_ME_PATH}"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        response = await client.post(
            url,
            json=payload,
            params={"access_token": settings.meta_page_access_token},
        )
        response.raise_for_status()
        body = response.json()
        outbound_event_id = body.get(META_RESPONSE_MESSAGE_ID_KEY, "")
        await bot_outbound_tracker.track(outbound_event_id)
        logger.info(LOG_MESSENGER_SENT_TEMPLATE, recipient_id)
        return True


async def generate_and_send_response(
    sender_id: str,
    plataforma: str,
    conversation_id: Optional[UUID] = None,
    session_id: Optional[UUID] = None,
    texto_override: Optional[str] = None,
) -> RespondResponse:
    context_id = conversation_id or session_id

    if texto_override:
        mensaje = texto_override
        campos_faltantes: list[str] = []
    elif context_id:
        data = await get_session_data(context_id)
        mensaje, campos_faltantes = build_response_message(data)
    else:
        mensaje = FALLBACK_HELLO_RESPONSE
        campos_faltantes = DEFAULT_CONTACT_FIELDS or DEFAULT_MISSING_FIELDS

    enviado = await send_meta_message(sender_id, plataforma, mensaje)
    logger.info(LOG_RESPONSE_GENERATED_TEMPLATE, mensaje[:80], enviado)
    return RespondResponse(
        mensaje=mensaje,
        enviado=enviado,
        campos_faltantes=campos_faltantes,
    )
