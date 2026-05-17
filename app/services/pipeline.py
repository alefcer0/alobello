import logging

from app.services.classifier import classify_text
from app.services.extractor import extract_data
from app.services.meta_webhook import IncomingMessage
from app.services.responder import generate_and_send_response
from app.services.session_manager import (
    get_or_create_session,
    merge_extracted_data,
    save_lead,
)

logger = logging.getLogger(__name__)


async def process_incoming_message(msg: IncomingMessage) -> dict:
    logger.info(
        "Processing message from %s [%s]: %s",
        msg.sender_id,
        msg.plataforma,
        msg.text[:100],
    )

    session = await get_or_create_session(msg.sender_id, msg.plataforma)
    is_first = session.created

    extracted = extract_data(msg.text)
    merged = await merge_extracted_data(session.id, extracted, is_first)

    classification = await classify_text(msg.text)
    datos_en = "primer_mensaje" if is_first else "respuesta"

    lead_id = await save_lead(
        session_id=session.id,
        plataforma=msg.plataforma,
        tipo=classification.categoria,
        mensaje=msg.text,
        nombre=msg.nombre,
        telefono=merged.telefono,
        cantidad=merged.cantidad,
        ciudad=merged.ciudad,
        datos_extraidos_en=datos_en,
    )

    response_sent = False
    response_message = None
    if classification.categoria == "lead":
        resp = await generate_and_send_response(
            sender_id=msg.sender_id,
            plataforma=msg.plataforma,
            session_id=session.id,
        )
        response_sent = resp.enviado
        response_message = resp.mensaje

    result = {
        "lead_id": str(lead_id),
        "session_id": str(session.id),
        "tipo": classification.categoria,
        "extraido": merged.model_dump(),
        "respuesta_enviada": response_sent,
        "respuesta": response_message,
    }
    logger.info("Pipeline complete: %s", result)
    return result
