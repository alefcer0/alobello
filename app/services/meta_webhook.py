import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Optional

from app.config import settings
from app.db.connection import get_connection

logger = logging.getLogger(__name__)


@dataclass
class IncomingMessage:
    event_id: str
    sender_id: str
    text: str
    plataforma: str
    nombre: Optional[str] = None


def verify_signature(payload: bytes, signature_header: Optional[str]) -> bool:
    if not signature_header or not settings.meta_app_secret:
        logger.warning("Missing signature or app secret")
        return False
    expected_prefix = "sha256="
    if not signature_header.startswith(expected_prefix):
        return False
    received = signature_header[len(expected_prefix) :]
    computed = hmac.new(
        settings.meta_app_secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    valid = hmac.compare_digest(received, computed)
    if not valid:
        logger.warning("Invalid Meta webhook signature")
    return valid


async def is_duplicate_event(event_id: str) -> bool:
    async with get_connection() as conn:
        existing = await conn.fetchval(
            "SELECT 1 FROM processed_events WHERE event_id = $1",
            event_id,
        )
        return existing is not None


async def mark_event_processed(event_id: str) -> None:
    async with get_connection() as conn:
        await conn.execute(
            "INSERT INTO processed_events (event_id) VALUES ($1) ON CONFLICT DO NOTHING",
            event_id,
        )


def _detect_platform(entry: dict) -> str:
    if entry.get("messaging"):
        return "facebook"
    if entry.get("changes"):
        change = entry["changes"][0]
        value = change.get("value", {})
        if value.get("messaging_product") == "whatsapp":
            return "whatsapp"
        if change.get("field") in ("comments", "feed"):
            return "instagram"
        if value.get("messages"):
            return "whatsapp"
    return "facebook"


def parse_meta_payload(body: dict) -> list[IncomingMessage]:
    messages: list[IncomingMessage] = []
    object_type = body.get("object", "")

    for entry in body.get("entry", []):
        plataforma = _detect_platform(entry)
        entry_id = entry.get("id", "unknown")

        if entry.get("messaging"):
            for idx, event in enumerate(entry["messaging"]):
                msg = _parse_messaging_event(event, plataforma, f"{entry_id}_{idx}")
                if msg:
                    messages.append(msg)

        for change in entry.get("changes", []):
            value = change.get("value", {})
            field = change.get("field", "")

            if field == "messages" or value.get("messages"):
                platform = "whatsapp" if value.get("messaging_product") == "whatsapp" else plataforma
                for idx, wa_msg in enumerate(value.get("messages", [])):
                    msg = _parse_whatsapp_message(wa_msg, value, platform, f"{entry_id}_wa_{idx}")
                    if msg:
                        messages.append(msg)

            if field in ("comments", "feed"):
                for idx, comment in enumerate(value.get("comments", value.get("item", [])) if isinstance(value.get("comments"), list) else []):
                    msg = _parse_comment(comment, f"{entry_id}_ig_{idx}")
                    if msg:
                        messages.append(msg)
                if value.get("text") and value.get("from"):
                    msg = _parse_comment(value, f"{entry_id}_ig_comment")
                    if msg:
                        messages.append(msg)

    if object_type == "instagram":
        for msg in messages:
            msg.plataforma = "instagram"

    logger.info("Parsed %d messages from webhook payload", len(messages))
    return messages


def _parse_messaging_event(event: dict, plataforma: str, event_id: str) -> Optional[IncomingMessage]:
    sender = event.get("sender", {})
    message = event.get("message", {})
    if not message or message.get("is_echo"):
        return None
    text = message.get("text", "")
    if not text:
        return None
    return IncomingMessage(
        event_id=event.get("mid", event_id),
        sender_id=sender.get("id", ""),
        text=text,
        plataforma=plataforma,
        nombre=None,
    )


def _parse_whatsapp_message(
    wa_msg: dict, value: dict, plataforma: str, event_id: str
) -> Optional[IncomingMessage]:
    if wa_msg.get("type") != "text":
        return None
    text = wa_msg.get("text", {}).get("body", "")
    if not text:
        return None
    contacts = value.get("contacts", [{}])
    nombre = contacts[0].get("profile", {}).get("name") if contacts else None
    return IncomingMessage(
        event_id=wa_msg.get("id", event_id),
        sender_id=wa_msg.get("from", ""),
        text=text,
        plataforma=plataforma,
        nombre=nombre,
    )


def _parse_comment(comment: dict, event_id: str) -> Optional[IncomingMessage]:
    text = comment.get("text") or comment.get("message", "")
    from_data = comment.get("from", {})
    sender_id = from_data.get("id", "") if isinstance(from_data, dict) else ""
    if not text or not sender_id:
        return None
    return IncomingMessage(
        event_id=comment.get("id", event_id),
        sender_id=sender_id,
        text=text,
        plataforma="instagram",
        nombre=from_data.get("username") if isinstance(from_data, dict) else None,
    )


def build_simulated_payload(sender_id: str, text: str, plataforma: str) -> dict:
    """Build a Meta-like payload for testing via Postman."""
    if plataforma == "whatsapp":
        return {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA_ID",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": settings.whatsapp_phone_number_id},
                                "contacts": [{"profile": {"name": "Cliente Prueba"}, "wa_id": sender_id}],
                                "messages": [
                                    {
                                        "from": sender_id,
                                        "id": f"wamid.test_{hash(text) % 100000}",
                                        "timestamp": "1715000000",
                                        "type": "text",
                                        "text": {"body": text},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
    return {
        "object": "page",
        "entry": [
            {
                "id": "PAGE_ID",
                "messaging": [
                    {
                        "sender": {"id": sender_id},
                        "recipient": {"id": "PAGE_ID"},
                        "timestamp": 1715000000,
                        "message": {
                            "mid": f"mid.test_{hash(text) % 100000}",
                            "text": text,
                        },
                    }
                ],
            }
        ],
    }
