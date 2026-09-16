import json
import logging

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.constants.routes import (
    HUB_CHALLENGE_ALIAS,
    HUB_MODE_ALIAS,
    HUB_MODE_SUBSCRIBE,
    HUB_VERIFY_TOKEN_ALIAS,
    LOG_WEBHOOK_BUFFERED_TEMPLATE,
    LOG_WEBHOOK_DUPLICATE_TEMPLATE,
    LOG_WEBHOOK_VERIFIED,
    LOG_WEBHOOK_VERIFY_FAILED,
    LOG_WEBHOOK_VERIFY_TEMPLATE,
    WEBHOOK_PATH,
    WEBHOOK_RATE_LIMIT,
    WEBHOOK_TAG,
)
from app.constants.common import CATEGORY_HUMAN_REPLY, DIRECTION_OUTBOUND
from app.constants.webhook import (
    DEFAULT_SIMULATED_PLATFORM,
    ENTRY_KEY,
    ERROR_INVALID_JSON,
    ERROR_INVALID_SIGNATURE,
    ERROR_VERIFICATION_FAILED,
    RESULT_KEY_PROCESSED,
    RESULT_KEY_QUEUED,
    RESULT_KEY_RESULTS,
    SIGNATURE_HEADER_NAME,
    SIMULATED_FLAG_KEY,
    SIMULATED_ENTRY_PLATFORM_KEY,
    SIMULATED_ENTRY_SENDER_KEY,
    SIMULATED_ENTRY_TEXT_KEY,
    STATUS_OK,
)
from app.limiter import limiter
from app.services.meta_webhook import (
    build_simulated_payload,
    is_duplicate_event,
    mark_event_processed,
    parse_meta_payload,
    verify_signature,
)
from app.services.bot_outbound_tracker import bot_outbound_tracker
from app.services.message_buffer import message_burst_buffer
from app.services.session_manager import (
    get_or_create_session,
    mark_human_intervention,
    save_message,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    WEBHOOK_PATH,
    tags=["Webhook Publico"],
    summary="Verificar suscripcion de Meta",
    description=(
        "Funcionalidad:\n"
        "Valida la suscripcion del webhook de Meta devolviendo el challenge en texto plano.\n\n"
        "Funcionamiento:\n"
        "- Recibe hub.mode, hub.verify_token y hub.challenge.\n"
        "- Si el verify_token coincide con la configuracion del servidor, responde el challenge tal cual.\n"
        "- Si no coincide, devuelve 403."
    ),
)
async def verify_webhook(
    hub_mode: str = Query(alias=HUB_MODE_ALIAS),
    hub_verify_token: str = Query(alias=HUB_VERIFY_TOKEN_ALIAS),
    hub_challenge: str = Query(alias=HUB_CHALLENGE_ALIAS),
):
    logger.info(LOG_WEBHOOK_VERIFY_TEMPLATE, hub_mode)
    if hub_mode == HUB_MODE_SUBSCRIBE and hub_verify_token == settings.meta_verify_token:
        logger.info(LOG_WEBHOOK_VERIFIED)
        return PlainTextResponse(content=hub_challenge)
    logger.warning(LOG_WEBHOOK_VERIFY_FAILED)
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_VERIFICATION_FAILED)


@router.post(
    WEBHOOK_PATH,
    tags=["Webhook Publico"],
    summary="Recibir evento entrante de Meta",
    description=(
        "Funcionalidad:\n"
        "Procesa mensajes entrantes de webhook y ejecuta el pipeline conversacional.\n\n"
        "Funcionamiento:\n"
        "- Acepta eventos reales de Meta y payload simulado para pruebas.\n"
        "- Verifica firma cuando aplica, evita duplicados y maneja buffering por rafagas.\n"
        "- Aplica handoff humano y devuelve resultado de procesamiento."
    ),
)
@limiter.limit(WEBHOOK_RATE_LIMIT)
async def receive_webhook(request: Request):
    raw_body = await request.body()
    signature = request.headers.get(SIGNATURE_HEADER_NAME)

    try:
        body = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_INVALID_JSON)

    is_simulated = body.get(SIMULATED_FLAG_KEY) or all(
        k in body
        for k in (
            SIMULATED_ENTRY_SENDER_KEY,
            SIMULATED_ENTRY_TEXT_KEY,
            SIMULATED_ENTRY_PLATFORM_KEY,
        )
    ) and ENTRY_KEY not in body

    if not is_simulated and settings.meta_app_secret:
        if not verify_signature(raw_body, signature):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_INVALID_SIGNATURE
            )

    if is_simulated:
        payload = build_simulated_payload(
            body[SIMULATED_ENTRY_SENDER_KEY],
            body[SIMULATED_ENTRY_TEXT_KEY],
            body.get(SIMULATED_ENTRY_PLATFORM_KEY, DEFAULT_SIMULATED_PLATFORM),
        )
    else:
        payload = body

    messages = parse_meta_payload(payload)
    results = []
    queued = 0

    for msg in messages:
        if await is_duplicate_event(msg.event_id):
            logger.info(LOG_WEBHOOK_DUPLICATE_TEMPLATE, msg.event_id)
            continue

        if not msg.is_customer_message:
            if await bot_outbound_tracker.consume_if_tracked(msg.event_id):
                await mark_event_processed(msg.event_id)
                continue

            if msg.sender_id:
                await mark_human_intervention(msg.sender_id, msg.plataforma)

                if msg.text:
                    session = await get_or_create_session(msg.sender_id, msg.plataforma)
                    await save_message(
                        session_id=session.id,
                        plataforma=msg.plataforma,
                        sender_id=msg.sender_id,
                        direction=DIRECTION_OUTBOUND,
                        texto=msg.text,
                        categoria=CATEGORY_HUMAN_REPLY,
                        external_message_id=msg.event_id,
                    )

            await mark_event_processed(msg.event_id)
            continue

        if msg.triggers_handoff and msg.sender_id:
            await mark_human_intervention(msg.sender_id, msg.plataforma)

        if await message_burst_buffer.has_pending_event(msg.event_id):
            logger.info(LOG_WEBHOOK_DUPLICATE_TEMPLATE, msg.event_id)
            continue

        flushed_results = await message_burst_buffer.enqueue_message(msg)
        if flushed_results:
            results.extend(flushed_results)
        else:
            queued += 1
            logger.info(LOG_WEBHOOK_BUFFERED_TEMPLATE, msg.event_id)

    return {
        "status": STATUS_OK,
        RESULT_KEY_PROCESSED: len(results),
        RESULT_KEY_QUEUED: queued,
        RESULT_KEY_RESULTS: results,
    }

