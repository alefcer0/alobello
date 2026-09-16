import re

BUYER_VENDOR_PATTERNS: tuple[str, ...] = (
    r"\b(me|nos)\s+pued(?:e|en)\s+vender\b",
    r"\b(me|nos)\s+vend(?:e|en|es)\b",
)

EXPLICIT_SUPPLIER_PATTERNS: tuple[str, ...] = (
    r"\b(yo\s+)?vendo\b",
    r"\bofrezco\b",
    r"\bdistribuyo\b",
    r"\bsoy\s+proveedor\b",
    r"\bsomos\s+proveedores\b",
    r"\bquiero\s+vender\b",
)

ORDER_INTENT_NORMALIZE_PATTERN = re.compile(r"[^a-z_]")
ORDER_INTENT_CONFIRMED_HINTS = {"si", "simon", "simn", "ok", "va"}
ORDER_INTENT_CANCELLED_HINTS = {"noc", "noneg", "ya_no"}
ORDER_INTENT_CONFIRMED_PREFIX = "confirm"
ORDER_INTENT_CANCELLED_PREFIX = "cancel"
ORDER_INTENT_NONE = "none"
