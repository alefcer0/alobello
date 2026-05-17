import logging

from fastapi import APIRouter

from app.models.lead import (
    ClassifyRequest,
    ClassifyResponse,
    ExtractRequest,
    ExtractResponse,
    RespondRequest,
    RespondResponse,
)
from app.models.session import SessionCreate, SessionResponse
from app.services.classifier import classify_text
from app.services.extractor import extract_data
from app.services.responder import generate_and_send_response
from app.services.session_manager import get_or_create_session

logger = logging.getLogger(__name__)
router = APIRouter(tags=["internal"])


@router.post("/classify", response_model=ClassifyResponse)
async def classify_endpoint(body: ClassifyRequest):
    logger.info("POST /classify")
    return await classify_text(body.texto)


@router.post("/extract", response_model=ExtractResponse)
async def extract_endpoint(body: ExtractRequest):
    logger.info("POST /extract")
    return extract_data(body.texto)


@router.post("/respond", response_model=RespondResponse)
async def respond_endpoint(body: RespondRequest):
    logger.info("POST /respond for sender=%s", body.sender_id)
    return await generate_and_send_response(
        sender_id=body.sender_id,
        plataforma=body.plataforma,
        session_id=body.session_id,
        texto_override=body.texto,
    )


@router.post("/session", response_model=SessionResponse)
async def session_endpoint(body: SessionCreate):
    logger.info("POST /session for sender=%s", body.sender_id)
    return await get_or_create_session(body.sender_id, body.plataforma)
