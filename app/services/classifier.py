import logging
import re

from openai import AsyncOpenAI

from app.config import settings
from app.models.lead import ClassifyResponse

logger = logging.getLogger(__name__)

LEAD_KEYWORDS = ["precio", "cuanto", "cuánto", "kilo", "nopal", "comprar", "pedido", "cotiz"]
PROVEEDOR_KEYWORDS = ["vendo", "ofrezco", "transporte", "distribuyo"]
SALUDO_KEYWORDS = ["hola", "buenos días", "buenas tardes", "buenas noches", "buen día", "qué tal", "que tal"]
VALID_CATEGORIES = {"lead", "proveedor", "saludo", "spam"}

_openai_client: AsyncOpenAI | None = None


def _get_openai() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client


def _normalize(text: str) -> str:
    return text.lower().strip()


def classify_by_rules(text: str) -> str | None:
    normalized = _normalize(text)
    for kw in LEAD_KEYWORDS:
        if kw in normalized:
            logger.info("Rule match: lead (keyword=%s)", kw)
            return "lead"
    for kw in PROVEEDOR_KEYWORDS:
        if kw in normalized:
            logger.info("Rule match: proveedor (keyword=%s)", kw)
            return "proveedor"
    for kw in SALUDO_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", normalized):
            logger.info("Rule match: saludo (keyword=%s)", kw)
            return "saludo"
    return None


async def classify_with_openai(text: str) -> str:
    logger.info("No rule match, calling OpenAI for classification")
    client = _get_openai()
    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "user",
                "content": (
                    "Clasifica este mensaje en: lead, proveedor, saludo o spam. "
                    "Responde solo con una palabra.\n\n" + text
                ),
            }
        ],
        max_tokens=10,
        temperature=0,
    )
    raw = (response.choices[0].message.content or "spam").strip().lower()
    category = raw.split()[0] if raw else "spam"
    if category not in VALID_CATEGORIES:
        logger.warning("OpenAI returned invalid category '%s', defaulting to spam", raw)
        category = "spam"
    logger.info("OpenAI classification: %s", category)
    return category


async def classify_text(text: str) -> ClassifyResponse:
    logger.info("Classifying message (len=%d)", len(text))
    rule_result = classify_by_rules(text)
    if rule_result:
        return ClassifyResponse(categoria=rule_result)
    category = await classify_with_openai(text)
    return ClassifyResponse(categoria=category)
