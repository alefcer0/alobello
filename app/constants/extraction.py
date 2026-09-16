import re

from app.constants.units import NOPAL_UNIT_PROMPT_VALUES, NOPAL_UNIT_TOKEN_PATTERN

OPENAI_EXTRACTION_PROMPT = (
    "Extrae datos de contacto y contexto del mensaje. "
    "Si hay intencion de compra de nopal, extrae tambien cantidad. "
    "La cantidad debe incluir numero y unidad canonica. "
    f"Unidades validas: {NOPAL_UNIT_PROMPT_VALUES}. "
    "Responde SOLO JSON con llaves: nombre, telefono, cantidad, ciudad, codigo_postal. "
    "Si no existe un dato, devuelve null. "
    "No inventes informacion."
)

PHONE_PATTERNS = [
    re.compile(r"(?:\+52\s?)?(?:\d{2,3}[\s.-]?)?\d{4}[\s.-]?\d{4}"),
    re.compile(r"\b\d{10}\b"),
    re.compile(r"\b\d{3}[\s.-]?\d{3}[\s.-]?\d{4}\b"),
]

QUANTITY_PATTERNS = [
    re.compile(
        rf"(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>{NOPAL_UNIT_TOKEN_PATTERN})\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?:necesito|quiero|pido|compro|ocupo)\s+(?:unos?\s+)?(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>{NOPAL_UNIT_TOKEN_PATTERN})?",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?P<value>\d+(?:[.,]\d+)?)\s*(?:(?P<unit>{NOPAL_UNIT_TOKEN_PATTERN})\s*)?(?:de\s+)?nopal",
        re.IGNORECASE,
    ),
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
    r"(?:en|desde|para|hacia)\s+([A-Za-zÁÉÍÓÚáéíóúñÑ\s]+?)(?:\s*[,.!?]|$)",
    re.IGNORECASE,
)

CITY_CONTEXT_PATTERN = re.compile(
    r"(?:soy\s+de|vivo\s+en|estoy\s+en|radico\s+en)\s+([A-Za-zÁÉÍÓÚáéíóúñÑ\s]+?)(?:\s*[,.!?]|$)",
    re.IGNORECASE,
)

CITY_NOISE_PREFIXES = (
    "nopal ",
    "nopales ",
    "kilos ",
    "kg ",
)

CP_PATTERN = re.compile(
    r"(?:cp|c\.p\.|codigo\s*postal|código\s*postal)\s*[:\-]?\s*(\d{5})",
    re.IGNORECASE,
)

NAME_PATTERNS = [
    re.compile(r"(?:me\s+llamo|mi\s+nombre\s+es|soy)\s+([A-Za-zÁÉÍÓÚáéíóúñÑ\s]{2,60})", re.IGNORECASE),
]

CP_PREFIX = "CP "
