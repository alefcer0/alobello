import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from app.config import settings
from app.limiter import limiter
from app.services.meta_webhook import (
    build_simulated_payload,
    is_duplicate_event,
    mark_event_processed,
    parse_meta_payload,
    verify_signature,
)
from app.services.pipeline import process_incoming_message

logger = logging.getLogger(__name__)
router = APIRouter(tags=["webhook"])


@router.get("/webhook/meta")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
):
    logger.info("Meta webhook verification attempt (mode=%s)", hub_mode)
    if hub_mode == "subscribe" and hub_verify_token == settings.meta_verify_token:
        logger.info("Webhook verified successfully")
        return int(hub_challenge)
    logger.warning("Webhook verification failed")
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verification failed")


@router.post("/webhook/meta")
@limiter.limit("60/minute")
async def receive_webhook(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("x-hub-signature-256")

    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON")

    is_simulated = body.get("simulated") or all(
        k in body for k in ("sender_id", "texto", "plataforma")
    ) and "entry" not in body

    if not is_simulated and settings.meta_app_secret:
        if not verify_signature(raw_body, signature):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature"
            )

    if is_simulated:
        payload = build_simulated_payload(
            body["sender_id"],
            body["texto"],
            body.get("plataforma", "whatsapp"),
        )
    else:
        payload = body

    messages = parse_meta_payload(payload)
    results = []

    for msg in messages:
        if await is_duplicate_event(msg.event_id):
            logger.info("Duplicate event ignored: %s", msg.event_id)
            continue

        result = await process_incoming_message(msg)
        await mark_event_processed(msg.event_id)
        results.append(result)

    return {"status": "ok", "processed": len(results), "results": results}

