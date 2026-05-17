import logging
import re
from typing import Optional

from app.models.lead import ExtractResponse

logger = logging.getLogger(__name__)

PHONE_PATTERNS = [
    re.compile(r"(?:\+52\s?)?(?:\d{2,3}[\s.-]?)?\d{4}[\s.-]?\d{4}"),
    re.compile(r"\b\d{10}\b"),
    re.compile(r"\b\d{3}[\s.-]?\d{3}[\s.-]?\d{4}\b"),
]

QUANTITY_PATTERNS = [
    re.compile(
        r"(\d+(?:[.,]\d+)?)\s*(?:kg|kgs|kilos?|kilogramos?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:necesito|quiero|pido|compro|ocupo)\s+(?:unos?\s+)?(\d+(?:[.,]\d+)?)\s*(?:kg|kilos?)?",
        re.IGNORECASE,
    ),
    re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:de\s+)?nopal", re.IGNORECASE),
]

CIUDADES_MX = [
    "ciudad de méxico", "cdmx", "méxico df", "puebla", "guadalajara", "monterrey",
    "tijuana", "león", "querétaro", "mérida", "cancún", "toluca", "chihuahua",
    "hermosillo", "saltillo", "aguascalientes", "morelia", "oaxaca", "veracruz",
    "cuernavaca", "tuxtla", "villahermosa", "culiacán", "mazatlán", "torreón",
    "san luis potosí", "zacatecas", "durango", "colima", "campeche", "chetumal",
    "tepic", "la paz", "irapuato", "celaya", "pachuca", "tlaxcala", "cuautla",
    "cuautitlán", "ecatepec", "naucalpan", "nezahualcóyotl", "toluca de lerdo",
]

CIUDAD_PATTERN = re.compile(
    r"(?:en|de|desde|para)\s+([A-Za-zÁÉÍÓÚáéíóúñÑ\s]+?)(?:\s*[,.!?]|$)",
    re.IGNORECASE,
)


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        return f"+52{digits}"
    if len(digits) == 12 and digits.startswith("52"):
        return f"+{digits}"
    return raw.strip()


def extract_phone(text: str) -> Optional[str]:
    for pattern in PHONE_PATTERNS:
        match = pattern.search(text)
        if match:
            return _normalize_phone(match.group(0))
    return None


def extract_quantity(text: str) -> Optional[str]:
    for pattern in QUANTITY_PATTERNS:
        match = pattern.search(text)
        if match:
            value = match.group(1).replace(",", ".")
            return f"{value} kg"
    return None


def extract_city(text: str) -> Optional[str]:
    lower = text.lower()
    for ciudad in CIUDADES_MX:
        if ciudad in lower:
            return ciudad.title()

    match = CIUDAD_PATTERN.search(text)
    if match:
        candidate = match.group(1).strip()
        if len(candidate) > 2 and len(candidate) < 50:
            return candidate.title()
    return None


def extract_data(text: str) -> ExtractResponse:
    logger.info("Extracting data from text (len=%d)", len(text))
    result = ExtractResponse(
        telefono=extract_phone(text),
        cantidad=extract_quantity(text),
        ciudad=extract_city(text),
    )
    logger.info(
        "Extraction result: telefono=%s cantidad=%s ciudad=%s",
        result.telefono,
        result.cantidad,
        result.ciudad,
    )
    return result
