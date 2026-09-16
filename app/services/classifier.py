import logging
import re

from openai import APIError, AsyncOpenAI, RateLimitError

from app.config import settings
from app.constants.classification import (
    BUY_INTENT_KEYWORDS,
    NEGATIVE_KEYWORDS,
    OFF_TOPIC_HINTS,
    OPENAI_CLASSIFICATION_PROMPT,
    OPENAI_ORDER_INTENT_PROMPT,
    OPENAI_TOPIC_PROMPT,
    PRODUCT_KEYWORDS,
    PROVEEDOR_KEYWORDS,
    SALUDO_KEYWORDS,
    STRONG_LEAD_KEYWORDS,
    VALID_CATEGORIES,
    VALID_ORDER_INTENTS,
)
from app.constants.classifier import (
    BUYER_VENDOR_PATTERNS,
    ORDER_INTENT_CANCELLED_PREFIX,
    EXPLICIT_SUPPLIER_PATTERNS,
    ORDER_INTENT_CONFIRMED_PREFIX,
    ORDER_INTENT_CANCELLED_HINTS,
    ORDER_INTENT_CONFIRMED_HINTS,
    ORDER_INTENT_NONE,
    ORDER_INTENT_NORMALIZE_PATTERN,
)
from app.constants.classifier_runtime import (
    LOG_CLASSIFY_MESSAGE_LEN_TEMPLATE,
    LOG_OPENAI_API_ERROR_TEMPLATE,
    LOG_OPENAI_CLASSIFICATION_CALL_TEMPLATE,
    LOG_OPENAI_CLASSIFICATION_RESULT_TEMPLATE,
    LOG_OPENAI_INVALID_CATEGORY_TEMPLATE,
    LOG_OPENAI_NOT_CONFIGURED_CLASSIFICATION,
    LOG_OPENAI_ORDER_API_ERROR_TEMPLATE,
    LOG_OPENAI_ORDER_INTENT_CALL,
    LOG_OPENAI_ORDER_RATE_LIMIT_TEMPLATE,
    LOG_OPENAI_ORDER_UNEXPECTED_ERROR_TEMPLATE,
    LOG_OPENAI_RATE_LIMIT_TEMPLATE,
    LOG_OPENAI_TOPIC_CALL_TEMPLATE,
    LOG_OPENAI_TOPIC_ERROR_TEMPLATE,
    LOG_OPENAI_TOPIC_RESULT_TEMPLATE,
    LOG_OPENAI_UNEXPECTED_ERROR_TEMPLATE,
    LOG_RULE_LEAD_PRODUCT_INTENT,
    LOG_RULE_LEAD_STRONG_TEMPLATE,
    LOG_RULE_PROVEEDOR_TEMPLATE,
    LOG_RULE_SALUDO_TEMPLATE,
    LOG_RULE_SPAM_OFFTOPIC_TEMPLATE,
    LOG_RULE_SPAM_TEMPLATE,
    OPENAI_CLASSIFICATION_MAX_TOKENS,
    OPENAI_EMPTY_KEY,
    OPENAI_FALLBACK_CATEGORY,
    OPENAI_ORDER_FALLBACK_INTENT,
    OPENAI_ORDER_INTENT_MAX_TOKENS,
    OPENAI_PLACEHOLDER_PREFIX,
    OPENAI_TEMPERATURE,
    OPENAI_TOPIC_MAX_TOKENS,
    OPENAI_USER_ROLE,
)
from app.constants.common import (
    CATEGORY_CONSULTA,
    CATEGORY_LEAD,
    CATEGORY_PROVEEDOR,
    CATEGORY_SALUDO,
    CATEGORY_SPAM,
)
from app.constants.session_runtime import ORDER_INTENT_CANCELLED, ORDER_INTENT_CONFIRMED
from app.models.lead import ClassifyResponse

logger = logging.getLogger(__name__)

_openai_client: AsyncOpenAI | None = None


def _get_openai() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client


def _normalize(text: str) -> str:
    return text.lower().strip()


def _contains_any(text: str, keywords: list[str]) -> str | None:
    for kw in keywords:
        if kw in text:
            return kw
    return None


def _contains_keyword_or_phrase(text: str, keyword: str) -> bool:
    token = keyword.strip().lower()
    if " " in token:
        return token in text
    return re.search(rf"\b{re.escape(token)}\b", text) is not None


def _is_negative(text: str) -> str | None:
    return _contains_any(text, NEGATIVE_KEYWORDS)


def _is_strong_lead(text: str) -> str | None:
    return _contains_any(text, STRONG_LEAD_KEYWORDS)


def _is_supplier_offer(text: str) -> str | None:
    is_buyer_vendor_phrase = any(re.search(pattern, text) for pattern in BUYER_VENDOR_PATTERNS)
    has_explicit_supplier_claim = any(re.search(pattern, text) for pattern in EXPLICIT_SUPPLIER_PATTERNS)

    for kw in PROVEEDOR_KEYWORDS:
        if not _contains_keyword_or_phrase(text, kw):
            continue

        # Avoid false positives like "me pueden vender ...".
        if is_buyer_vendor_phrase and not has_explicit_supplier_claim:
            continue

        return kw

    return None


def _is_product_with_intent(text: str) -> bool:
    has_product = _contains_any(text, PRODUCT_KEYWORDS)
    has_intent = _contains_any(text, BUY_INTENT_KEYWORDS)
    return bool(has_product and has_intent)


def _is_plain_greeting_text(text: str) -> bool:
    words = re.findall(r"\w+", text, flags=re.UNICODE)
    if not words:
        return False

    if len(words) > 6:
        return False

    # Signals that the message is already a request rather than a simple greeting.
    request_signals = {
        "quiero",
        "necesito",
        "precio",
        "cotiza",
        "cotizar",
        "busco",
        "informacion",
        "info",
        "puedes",
        "podrias",
        "puede",
        "puedan",
        "costo",
        "cobrar",
        "contratar",
        "servicio",
    }
    if any(word in request_signals for word in words):
        return False

    return True


def classify_by_rules(text: str) -> str | None:
    normalized = _normalize(text)

    negative = _is_negative(normalized)
    if negative:
        logger.info(LOG_RULE_SPAM_TEMPLATE, negative)
        return CATEGORY_SPAM

    proveedor = _is_supplier_offer(normalized)
    if proveedor:
        logger.info(LOG_RULE_PROVEEDOR_TEMPLATE, proveedor)
        return CATEGORY_PROVEEDOR

    if _is_product_with_intent(normalized):
        logger.info(LOG_RULE_LEAD_PRODUCT_INTENT)
        return CATEGORY_LEAD

    strong = _is_strong_lead(normalized)
    if strong and _contains_any(normalized, PRODUCT_KEYWORDS):
        logger.info(LOG_RULE_LEAD_STRONG_TEMPLATE, strong)
        return CATEGORY_LEAD

    for kw in SALUDO_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", normalized):
            if _is_plain_greeting_text(normalized):
                logger.info(LOG_RULE_SALUDO_TEMPLATE, kw)
                return CATEGORY_SALUDO
            logger.info(LOG_RULE_SALUDO_TEMPLATE, kw)
            return CATEGORY_CONSULTA

    off_topic = _contains_any(normalized, OFF_TOPIC_HINTS)
    if off_topic:
        logger.info(LOG_RULE_SPAM_OFFTOPIC_TEMPLATE, off_topic)
        return CATEGORY_SPAM

    return None


def _openai_configured() -> bool:
    key = (settings.openai_api_key or "").strip()
    return bool(key) and not key.startswith(OPENAI_PLACEHOLDER_PREFIX) and key != OPENAI_EMPTY_KEY


async def classify_with_openai(text: str) -> str:
    category, _ = await classify_with_openai_result(text)
    return category


async def classify_with_openai_result(text: str) -> tuple[str, bool]:
    if not _openai_configured():
        logger.warning(LOG_OPENAI_NOT_CONFIGURED_CLASSIFICATION)
        return OPENAI_FALLBACK_CATEGORY, False

    logger.info(LOG_OPENAI_CLASSIFICATION_CALL_TEMPLATE, settings.openai_model)
    try:
        client = _get_openai()
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": OPENAI_USER_ROLE,
                    "content": OPENAI_CLASSIFICATION_PROMPT + text,
                }
            ],
            max_tokens=OPENAI_CLASSIFICATION_MAX_TOKENS,
            temperature=OPENAI_TEMPERATURE,
        )
        raw = (response.choices[0].message.content or OPENAI_FALLBACK_CATEGORY).strip().lower()
        category = raw.split()[0] if raw else OPENAI_FALLBACK_CATEGORY
        if category not in VALID_CATEGORIES:
            logger.warning(LOG_OPENAI_INVALID_CATEGORY_TEMPLATE, raw)
            category = OPENAI_FALLBACK_CATEGORY

        # Prevent long request messages from being mislabeled as plain greeting.
        if category == CATEGORY_SALUDO and not _is_plain_greeting_text(_normalize(text)):
            category = CATEGORY_CONSULTA

        logger.info(LOG_OPENAI_CLASSIFICATION_RESULT_TEMPLATE, category)
        return category, True
    except RateLimitError as exc:
        logger.error(LOG_OPENAI_RATE_LIMIT_TEMPLATE, exc)
        return OPENAI_FALLBACK_CATEGORY, False
    except APIError as exc:
        logger.error(LOG_OPENAI_API_ERROR_TEMPLATE, exc)
        return OPENAI_FALLBACK_CATEGORY, False
    except Exception as exc:
        logger.error(LOG_OPENAI_UNEXPECTED_ERROR_TEMPLATE, exc)
        return OPENAI_FALLBACK_CATEGORY, False


def _normalize_order_intent(raw: str) -> str:
    token = (
        ORDER_INTENT_NORMALIZE_PATTERN.sub("", (raw or "").strip().lower().split()[0])
        if raw
        else ORDER_INTENT_NONE
    )
    if token in VALID_ORDER_INTENTS:
        return token

    if token.startswith(ORDER_INTENT_CONFIRMED_PREFIX) or token in ORDER_INTENT_CONFIRMED_HINTS:
        return ORDER_INTENT_CONFIRMED
    if token.startswith(ORDER_INTENT_CANCELLED_PREFIX) or token in ORDER_INTENT_CANCELLED_HINTS:
        return ORDER_INTENT_CANCELLED
    return ORDER_INTENT_NONE


async def classify_order_intent_with_openai(text: str, order_status: str) -> str | None:
    if not _openai_configured():
        return None

    logger.info(LOG_OPENAI_ORDER_INTENT_CALL)
    try:
        client = _get_openai()
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": OPENAI_USER_ROLE,
                    "content": (
                        f"{OPENAI_ORDER_INTENT_PROMPT}"
                        f"Estado actual de la orden: {order_status}\n"
                        f"Mensaje del cliente: {text}"
                    ),
                }
            ],
            max_tokens=OPENAI_ORDER_INTENT_MAX_TOKENS,
            temperature=OPENAI_TEMPERATURE,
        )
        raw = response.choices[0].message.content or OPENAI_ORDER_FALLBACK_INTENT
        intent = _normalize_order_intent(raw)
        if intent in {ORDER_INTENT_CONFIRMED, ORDER_INTENT_CANCELLED}:
            return intent
        return None
    except RateLimitError as exc:
        logger.error(LOG_OPENAI_ORDER_RATE_LIMIT_TEMPLATE, exc)
        return None
    except APIError as exc:
        logger.error(LOG_OPENAI_ORDER_API_ERROR_TEMPLATE, exc)
        return None
    except Exception as exc:
        logger.error(LOG_OPENAI_ORDER_UNEXPECTED_ERROR_TEMPLATE, exc)
        return None


def _normalize_topic(raw: str) -> str | None:
    topic = re.sub(r"\s+", " ", (raw or "").strip().lower()).strip(" .,:;!?\"'")
    if not topic:
        return None
    if len(topic) > 80:
        topic = topic[:80].rstrip()
    return topic


async def infer_topic_with_openai(text: str, category_hint: str | None = None) -> str | None:
    if not _openai_configured():
        return None

    try:
        client = _get_openai()
        logger.info(LOG_OPENAI_TOPIC_CALL_TEMPLATE, settings.openai_model)
        prefix = f"Categoria detectada: {category_hint}\n" if category_hint else ""
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": OPENAI_USER_ROLE,
                    "content": f"{prefix}{OPENAI_TOPIC_PROMPT}{text}",
                }
            ],
            max_tokens=OPENAI_TOPIC_MAX_TOKENS,
            temperature=OPENAI_TEMPERATURE,
        )
        topic = _normalize_topic(response.choices[0].message.content or "")
        if topic:
            logger.info(LOG_OPENAI_TOPIC_RESULT_TEMPLATE, topic)
        return topic
    except (RateLimitError, APIError, Exception) as exc:
        logger.error(LOG_OPENAI_TOPIC_ERROR_TEMPLATE, exc)
        return None


async def classify_text(text: str) -> ClassifyResponse:
    logger.info(LOG_CLASSIFY_MESSAGE_LEN_TEMPLATE, len(text))
    rule_result = classify_by_rules(text)
    if rule_result:
        return ClassifyResponse(categoria=rule_result)
    category = await classify_with_openai(text)
    return ClassifyResponse(categoria=category)
