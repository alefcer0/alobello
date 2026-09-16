import re
from enum import Enum


class NopalUnit(str, Enum):
    KG = "kg"
    TONELADA = "tonelada"
    CAJA = "caja"
    COSTAL = "costal"
    BULTO = "bulto"
    MANOJO = "manojo"
    PENCA = "penca"
    PIEZA = "pieza"


DEFAULT_NOPAL_UNIT = NopalUnit.KG.value

NOPAL_UNIT_ALIASES: dict[str, str] = {
    "kg": NopalUnit.KG.value,
    "kgs": NopalUnit.KG.value,
    "kilo": NopalUnit.KG.value,
    "kilos": NopalUnit.KG.value,
    "kilogramo": NopalUnit.KG.value,
    "kilogramos": NopalUnit.KG.value,
    "ton": NopalUnit.TONELADA.value,
    "tons": NopalUnit.TONELADA.value,
    "tonelada": NopalUnit.TONELADA.value,
    "toneladas": NopalUnit.TONELADA.value,
    "caja": NopalUnit.CAJA.value,
    "cajas": NopalUnit.CAJA.value,
    "costal": NopalUnit.COSTAL.value,
    "costales": NopalUnit.COSTAL.value,
    "bulto": NopalUnit.BULTO.value,
    "bultos": NopalUnit.BULTO.value,
    "manojo": NopalUnit.MANOJO.value,
    "manojos": NopalUnit.MANOJO.value,
    "penca": NopalUnit.PENCA.value,
    "pencas": NopalUnit.PENCA.value,
    "pieza": NopalUnit.PIEZA.value,
    "piezas": NopalUnit.PIEZA.value,
}

NOPAL_UNIT_VALUES: tuple[str, ...] = tuple(unit.value for unit in NopalUnit)
NOPAL_UNIT_PROMPT_VALUES = ", ".join(NOPAL_UNIT_VALUES)

NOPAL_UNIT_TOKEN_PATTERN = "|".join(
    sorted((re.escape(token) for token in NOPAL_UNIT_ALIASES.keys()), key=len, reverse=True)
)
NOPAL_UNIT_DETECTION_REGEX = re.compile(rf"\b({NOPAL_UNIT_TOKEN_PATTERN})\b", re.IGNORECASE)


def normalize_nopal_unit(raw_unit: str | None) -> str | None:
    if not raw_unit:
        return None
    return NOPAL_UNIT_ALIASES.get(raw_unit.strip().lower())
