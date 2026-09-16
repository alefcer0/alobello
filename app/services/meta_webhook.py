import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Optional

from app.config import settings
from app.constants.common import PLATFORM_FACEBOOK, PLATFORM_INSTAGRAM, PLATFORM_WHATSAPP
from app.constants.meta_webhook_runtime import (
    DUPLICATE_EVENT_SELECT_SQL,
    EVENT_TIMESTAMP_FALLBACK,
    LOG_INVALID_SIGNATURE,
    LOG_MISSING_SIGNATURE_OR_SECRET,
    LOG_PARSED_MESSAGES_TEMPLATE,
    MARK_PROCESSED_INSERT_SQL,
    UNKNOWN_ENTRY_ID,
    UTF8_ENCODING,
)
from app.constants.payload_keys import (
    KEY_BODY,
    KEY_CHANGES,
    KEY_COMMENTS,
    KEY_CONTACTS,
    KEY_ENTRY,
    KEY_FIELD,
    KEY_FROM,
    KEY_ID,
    KEY_MID,
    KEY_IS_ECHO,
    KEY_ITEM,
    KEY_MESSAGE,
    KEY_MESSAGES,
    KEY_METADATA,
    KEY_MESSAGING,
    KEY_STANDBY,
    KEY_NAME,
    KEY_OBJECT,
    KEY_PHONE_NUMBER_ID,
    KEY_PROFILE,
    KEY_RECIPIENT,
    KEY_SENDER,
    KEY_TEXT,
    KEY_TIMESTAMP,
    KEY_TYPE,
    KEY_USERNAME,
    KEY_VALUE,
    KEY_WA_ID,
)
from app.constants.webhook import (
    MESSAGE_TYPE_TEXT,
    MESSAGING_PRODUCT_WHATSAPP,
    SIGNATURE_PREFIX,
    SIMULATED_CLIENT_NAME,
    SIMULATED_MID_TEMPLATE,
    SIMULATED_PAGE_ID,
    SIMULATED_WABA_ID,
    SIMULATED_WAMID_TEMPLATE,
    WEBHOOK_FIELD_COMMENTS,
    WEBHOOK_FIELD_FEED,
    WEBHOOK_FIELD_MESSAGES,
    WEBHOOK_OBJECT_INSTAGRAM,
    WEBHOOK_OBJECT_PAGE,
    WEBHOOK_OBJECT_WABA,
)
from app.db.connection import get_connection

logger = logging.getLogger(__name__)


@dataclass
class IncomingMessage:
    event_id: str
    sender_id: str
    text: str
    plataforma: str
    nombre: Optional[str] = None
    is_customer_message: bool = True
    triggers_handoff: bool = False


def verify_signature(payload: bytes, signature_header: Optional[str]) -> bool:
    if not signature_header or not settings.meta_app_secret:
        logger.warning(LOG_MISSING_SIGNATURE_OR_SECRET)
        return False
    if not signature_header.startswith(SIGNATURE_PREFIX):
        return False
    received = signature_header[len(SIGNATURE_PREFIX) :]
    computed = hmac.new(
        settings.meta_app_secret.encode(UTF8_ENCODING),
        payload,
        hashlib.sha256,
    ).hexdigest()
    valid = hmac.compare_digest(received, computed)
    if not valid:
        logger.warning(LOG_INVALID_SIGNATURE)
    return valid


async def is_duplicate_event(event_id: str) -> bool:
    async with get_connection() as conn:
        existing = await conn.fetchval(
            DUPLICATE_EVENT_SELECT_SQL,
            event_id,
        )
        return existing is not None


async def mark_event_processed(event_id: str) -> None:
    async with get_connection() as conn:
        await conn.execute(
            MARK_PROCESSED_INSERT_SQL,
            event_id,
        )


def _detect_platform(entry: dict) -> str:
    if entry.get(KEY_MESSAGING) or entry.get(KEY_STANDBY):
        return PLATFORM_FACEBOOK
    if entry.get(KEY_CHANGES):
        change = entry[KEY_CHANGES][0]
        value = change.get(KEY_VALUE, {})
        if value.get("messaging_product") == MESSAGING_PRODUCT_WHATSAPP:
            return PLATFORM_WHATSAPP
        if change.get(KEY_FIELD) in (WEBHOOK_FIELD_COMMENTS, WEBHOOK_FIELD_FEED):
            return PLATFORM_INSTAGRAM
        if value.get(WEBHOOK_FIELD_MESSAGES):
            return PLATFORM_WHATSAPP
    return PLATFORM_FACEBOOK


def parse_meta_payload(body: dict) -> list[IncomingMessage]:
    messages: list[IncomingMessage] = []
    object_type = body.get(KEY_OBJECT, "")

    for entry in body.get(KEY_ENTRY, []):
        plataforma = _detect_platform(entry)
        entry_id = entry.get(KEY_ID, UNKNOWN_ENTRY_ID)

        if entry.get(KEY_MESSAGING):
            for idx, event in enumerate(entry[KEY_MESSAGING]):
                msg = _parse_messaging_event(event, plataforma, f"{entry_id}_{idx}")
                if msg:
                    messages.append(msg)

        if entry.get(KEY_STANDBY):
            for idx, event in enumerate(entry[KEY_STANDBY]):
                msg = _parse_messaging_event(
                    event,
                    plataforma,
                    f"{entry_id}_standby_{idx}",
                    from_standby=True,
                )
                if msg:
                    messages.append(msg)

        for change in entry.get(KEY_CHANGES, []):
            value = change.get(KEY_VALUE, {})
            field = change.get(KEY_FIELD, "")

            if field == WEBHOOK_FIELD_MESSAGES or value.get(WEBHOOK_FIELD_MESSAGES):
                platform = (
                    PLATFORM_WHATSAPP
                    if value.get("messaging_product") == MESSAGING_PRODUCT_WHATSAPP
                    else plataforma
                )
                for idx, wa_msg in enumerate(value.get(WEBHOOK_FIELD_MESSAGES, [])):
                    msg = _parse_whatsapp_message(wa_msg, value, platform, f"{entry_id}_wa_{idx}")
                    if msg:
                        messages.append(msg)

            if field in (WEBHOOK_FIELD_COMMENTS, WEBHOOK_FIELD_FEED):
                for idx, comment in enumerate(value.get(KEY_COMMENTS, value.get(KEY_ITEM, [])) if isinstance(value.get(KEY_COMMENTS), list) else []):
                    msg = _parse_comment(comment, f"{entry_id}_ig_{idx}")
                    if msg:
                        messages.append(msg)
                if value.get(KEY_TEXT) and value.get(KEY_FROM):
                    msg = _parse_comment(value, f"{entry_id}_ig_comment")
                    if msg:
                        messages.append(msg)

    if object_type == WEBHOOK_OBJECT_INSTAGRAM:
        for msg in messages:
            msg.plataforma = PLATFORM_INSTAGRAM

    logger.info(LOG_PARSED_MESSAGES_TEMPLATE, len(messages))
    return messages


def _parse_messaging_event(
    event: dict,
    plataforma: str,
    event_id: str,
    *,
    from_standby: bool = False,
) -> Optional[IncomingMessage]:
    sender = event.get(KEY_SENDER, {})
    message = event.get(KEY_MESSAGE, {})
    if not message:
        return None
    is_echo = bool(message.get(KEY_IS_ECHO))

    text = message.get(KEY_TEXT, "")
    if not text and not (is_echo or from_standby):
        return None

    # Messenger puts message id inside message.mid (not at event root).
    # Include timestamp as fallback entropy to avoid false duplicates.
    message_id = message.get(KEY_MID) or f"{event_id}_{event.get(KEY_TIMESTAMP, EVENT_TIMESTAMP_FALLBACK)}"
    sender_id = sender.get(KEY_ID, "")

    if is_echo:
        sender_id = event.get(KEY_RECIPIENT, {}).get(KEY_ID, sender_id)

    return IncomingMessage(
        event_id=message_id,
        sender_id=sender_id,
        text=text,
        plataforma=plataforma,
        nombre=None,
        is_customer_message=not is_echo,
        triggers_handoff=from_standby or is_echo,
    )


def _parse_whatsapp_message(
    wa_msg: dict, value: dict, plataforma: str, event_id: str
) -> Optional[IncomingMessage]:
    if wa_msg.get(KEY_TYPE) != MESSAGE_TYPE_TEXT:
        return None
    text = wa_msg.get(KEY_TEXT, {}).get(KEY_BODY, "")
    if not text:
        return None
    contacts = value.get(KEY_CONTACTS, [{}])
    nombre = contacts[0].get(KEY_PROFILE, {}).get(KEY_NAME) if contacts else None
    return IncomingMessage(
        event_id=wa_msg.get(KEY_ID, event_id),
        sender_id=wa_msg.get(KEY_FROM, ""),
        text=text,
        plataforma=plataforma,
        nombre=nombre,
    )


def _parse_comment(comment: dict, event_id: str) -> Optional[IncomingMessage]:
    text = comment.get(KEY_TEXT) or comment.get(KEY_MESSAGE, "")
    from_data = comment.get(KEY_FROM, {})
    sender_id = from_data.get(KEY_ID, "") if isinstance(from_data, dict) else ""
    if not text or not sender_id:
        return None
    return IncomingMessage(
        event_id=comment.get(KEY_ID, event_id),
        sender_id=sender_id,
        text=text,
        plataforma=PLATFORM_INSTAGRAM,
        nombre=from_data.get(KEY_USERNAME) if isinstance(from_data, dict) else None,
    )


def build_simulated_payload(sender_id: str, text: str, plataforma: str) -> dict:
    """Build a Meta-like payload for testing via Postman."""
    nonce_ms = int(time.time() * 1000)
    text_hash = abs(hash(text)) % 100000

    if plataforma == PLATFORM_WHATSAPP:
        return {
            KEY_OBJECT: WEBHOOK_OBJECT_WABA,
            KEY_ENTRY: [
                {
                    KEY_ID: SIMULATED_WABA_ID,
                    KEY_CHANGES: [
                        {
                            KEY_FIELD: WEBHOOK_FIELD_MESSAGES,
                            KEY_VALUE: {
                                "messaging_product": MESSAGING_PRODUCT_WHATSAPP,
                                KEY_METADATA: {KEY_PHONE_NUMBER_ID: settings.whatsapp_phone_number_id},
                                KEY_CONTACTS: [{KEY_PROFILE: {KEY_NAME: SIMULATED_CLIENT_NAME}, KEY_WA_ID: sender_id}],
                                KEY_MESSAGES: [
                                    {
                                        KEY_FROM: sender_id,
                                        KEY_ID: SIMULATED_WAMID_TEMPLATE.format(
                                            sender_id=sender_id,
                                            text_hash=text_hash,
                                            nonce_ms=nonce_ms,
                                        ),
                                        KEY_TIMESTAMP: str(int(nonce_ms / 1000)),
                                        KEY_TYPE: MESSAGE_TYPE_TEXT,
                                        KEY_TEXT: {KEY_BODY: text},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
    return {
        KEY_OBJECT: WEBHOOK_OBJECT_PAGE,
        KEY_ENTRY: [
            {
                KEY_ID: SIMULATED_PAGE_ID,
                KEY_MESSAGING: [
                    {
                        KEY_SENDER: {KEY_ID: sender_id},
                        KEY_RECIPIENT: {KEY_ID: SIMULATED_PAGE_ID},
                        KEY_TIMESTAMP: nonce_ms,
                        KEY_MESSAGE: {
                            "mid": SIMULATED_MID_TEMPLATE.format(
                                sender_id=sender_id,
                                text_hash=text_hash,
                                nonce_ms=nonce_ms,
                            ),
                            KEY_TEXT: text,
                        },
                    }
                ],
            }
        ],
    }
