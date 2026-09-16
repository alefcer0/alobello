import logging
import json
import re
from typing import Optional

from openai import APIError, AsyncOpenAI, RateLimitError

from app.config import settings
from app.constants.extraction import (
    CITY_CONTEXT_PATTERN,
    CITY_NOISE_PREFIXES,
    CIUDAD_PATTERN,
    CIUDADES_MX,
    CP_PATTERN,
    CP_PREFIX,
    NAME_PATTERNS,
    OPENAI_EXTRACTION_PROMPT,
    PHONE_PATTERNS,
    QUANTITY_PATTERNS,
)
from app.constants.extractor_runtime import (
    CITY_CP_PREFIX,
    COUNTRY_CODE_MX,
    JSON_FALLBACK_EMPTY_OBJECT,
    JSON_OBJECT_REGEX,
    LOG_EXTRACTION_API_ERROR_TEMPLATE,
    LOG_EXTRACTION_MERGED_TEMPLATE,
    LOG_EXTRACTION_OPENAI_NOT_CONFIGURED,
    LOG_EXTRACTION_RATE_LIMIT_TEMPLATE,
    LOG_EXTRACTION_RESULT_TEMPLATE,
    LOG_EXTRACTION_TEXT_LEN_TEMPLATE,
    LOG_EXTRACTION_UNEXPECTED_ERROR_TEMPLATE,
    OPENAI_EXTRACTION_FALLBACK_RAW,
    OPENAI_EXTRACTION_MAX_TOKENS,
    OPENAI_EXTRACTION_TEMPERATURE,
    OPENAI_EMPTY_KEY,
    OPENAI_JSON_OBJECT,
    OPENAI_MESSAGE_LABEL,
    OPENAI_PLACEHOLDER_PREFIX,
    OPENAI_USER_ROLE,
    PLUS_COUNTRY_CODE,
    PLUS_PREFIX,
)
from app.constants.units import (
    DEFAULT_NOPAL_UNIT,
    NOPAL_UNIT_DETECTION_REGEX,
    normalize_nopal_unit,
)
from app.models.lead import ExtractResponse

logger = logging.getLogger(__name__)

_openai_client: AsyncOpenAI | None = None


def _get_openai() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client


def _openai_configured() -> bool:
    key = (settings.openai_api_key or "").strip()
    return bool(key) and not key.startswith(OPENAI_PLACEHOLDER_PREFIX) and key != OPENAI_EMPTY_KEY


def _extract_json_object(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(JSON_OBJECT_REGEX, raw, re.DOTALL)
        if not match:
            return json.loads(JSON_FALLBACK_EMPTY_OBJECT)
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return json.loads(JSON_FALLBACK_EMPTY_OBJECT)


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        return f"{PLUS_COUNTRY_CODE}{digits}"
    if len(digits) == 12 and digits.startswith(COUNTRY_CODE_MX):
        return f"{PLUS_PREFIX}{digits}"
    return raw.strip()


def extract_phone(text: str) -> Optional[str]:
    for pattern in PHONE_PATTERNS:
        match = pattern.search(text)
        if match:
            return _normalize_phone(match.group(0))
    return None


def extract_name(text: str) -> Optional[str]:
    for pattern in NAME_PATTERNS:
        match = pattern.search(text)
        if match:
            name = re.sub(r"\s+", " ", match.group(1)).strip(" .,!?")
            if 2 <= len(name) <= 80:
                return name.title()
    return None


def extract_quantity(text: str) -> Optional[str]:
    for pattern in QUANTITY_PATTERNS:
        match = pattern.search(text)
        if match:
            groups = match.groupdict()
            value = (groups.get("value") or match.group(1)).replace(",", ".")
            unit = normalize_nopal_unit(groups.get("unit")) or DEFAULT_NOPAL_UNIT
            return f"{value} {unit}"
    return None


def _clean_city_candidate(candidate: str) -> Optional[str]:
    cleaned = re.sub(r"\s+", " ", candidate).strip(" .,!?")
    lowered = cleaned.lower()

    for prefix in CITY_NOISE_PREFIXES:
        if lowered.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip(" .,!?")
            lowered = cleaned.lower()

    if len(cleaned) < 3 or len(cleaned) > 50:
        return None
    return cleaned.title()


def extract_city(text: str) -> Optional[str]:
    lower = text.lower()
    for ciudad in CIUDADES_MX:
        if ciudad in lower:
            return ciudad.title()

    context_match = CITY_CONTEXT_PATTERN.search(text)
    if context_match:
        candidate = _clean_city_candidate(context_match.group(1))
        if candidate:
            return candidate

    match = CIUDAD_PATTERN.search(text)
    if match:
        candidate = _clean_city_candidate(match.group(1))
        if candidate:
            return candidate
    return None


def extract_postal_code(text: str) -> Optional[str]:
    match = CP_PATTERN.search(text)
    if not match:
        return None
    return f"{CP_PREFIX}{match.group(1)}"


def _normalize_quantity(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return f"{value} {DEFAULT_NOPAL_UNIT}"

    raw = str(value).strip()
    if not raw:
        return None

    parsed = extract_quantity(raw)
    if parsed:
        return parsed

    number_match = re.search(r"\d+(?:[.,]\d+)?", raw)
    if number_match:
        detected_unit = None
        unit_match = NOPAL_UNIT_DETECTION_REGEX.search(raw)
        if unit_match:
            detected_unit = normalize_nopal_unit(unit_match.group(1))
        unit = detected_unit or DEFAULT_NOPAL_UNIT
        return f"{number_match.group(0).replace(',', '.')} {unit}"
    return None


def _merge_extracted(base: ExtractResponse, extra: ExtractResponse) -> ExtractResponse:
    return ExtractResponse(
        nombre=base.nombre or extra.nombre,
        telefono=base.telefono or extra.telefono,
        cantidad=base.cantidad or extra.cantidad,
        ciudad=base.ciudad or extra.ciudad,
    )


async def extract_data_with_openai(
    text: str,
    base: Optional[ExtractResponse] = None,
) -> ExtractResponse:
    current = base or ExtractResponse()
    if not _openai_configured():
        logger.warning(LOG_EXTRACTION_OPENAI_NOT_CONFIGURED)
        return current

    try:
        client = _get_openai()
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": OPENAI_USER_ROLE,
                    "content": f"{OPENAI_EXTRACTION_PROMPT}\n\n{OPENAI_MESSAGE_LABEL}\n{text}",
                }
            ],
            temperature=OPENAI_EXTRACTION_TEMPERATURE,
            max_tokens=OPENAI_EXTRACTION_MAX_TOKENS,
            response_format={"type": OPENAI_JSON_OBJECT},
        )
        raw = response.choices[0].message.content or OPENAI_EXTRACTION_FALLBACK_RAW
        data = _extract_json_object(raw)

        ciudad = data.get("ciudad")
        codigo_postal = data.get("codigo_postal")
        if not ciudad and codigo_postal:
            cp_digits = re.sub(r"\D", "", str(codigo_postal))
            ciudad = f"{CITY_CP_PREFIX}{cp_digits}" if len(cp_digits) == 5 else None

        ciudad_value = None
        if ciudad:
            ciudad_text = str(ciudad).strip()
            ciudad_value = (
                ciudad_text
                if ciudad_text.upper().startswith(CITY_CP_PREFIX)
                else ciudad_text.title()
            )

        ai_result = ExtractResponse(
            nombre=str(data.get("nombre")).strip().title() if data.get("nombre") else None,
            telefono=_normalize_phone(str(data.get("telefono"))) if data.get("telefono") else None,
            cantidad=_normalize_quantity(data.get("cantidad")),
            ciudad=ciudad_value,
        )
        merged = _merge_extracted(current, ai_result)
        logger.info(
            LOG_EXTRACTION_MERGED_TEMPLATE,
            merged.nombre,
            merged.telefono,
            merged.cantidad,
            merged.ciudad,
        )
        return merged
    except RateLimitError as exc:
        logger.error(LOG_EXTRACTION_RATE_LIMIT_TEMPLATE, exc)
        return current
    except APIError as exc:
        logger.error(LOG_EXTRACTION_API_ERROR_TEMPLATE, exc)
        return current
    except Exception as exc:
        logger.error(LOG_EXTRACTION_UNEXPECTED_ERROR_TEMPLATE, exc)
        return current


def extract_data(text: str) -> ExtractResponse:
    logger.info(LOG_EXTRACTION_TEXT_LEN_TEMPLATE, len(text))
    ciudad = extract_city(text) or extract_postal_code(text)
    result = ExtractResponse(
        nombre=extract_name(text),
        telefono=extract_phone(text),
        cantidad=extract_quantity(text),
        ciudad=ciudad,
    )
    logger.info(
        LOG_EXTRACTION_RESULT_TEMPLATE,
        result.nombre,
        result.telefono,
        result.cantidad,
        result.ciudad,
    )
    return result
