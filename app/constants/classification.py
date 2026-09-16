"""
Palabras clave y reglas para clasificación por capas (antes de OpenAI).

Orden de evaluación en classifier.classify_by_rules:
  1. NEGATIVE_KEYWORDS → spam
    2. PROVEEDOR_KEYWORDS → proveedor
    3. PRODUCT_KEYWORDS + BUY_INTENT_KEYWORDS → lead
    4. STRONG_LEAD_KEYWORDS + PRODUCT_KEYWORDS → lead
        5. SALUDO_KEYWORDS → saludo o consulta
  6. OFF_TOPIC_HINTS → spam
"""

# Hostil / sabotaje — prioridad máxima
NEGATIVE_KEYWORDS: list[str] = [
    "mierda",
    "basura",
    "estafa",
    "fraude",
    "ladrones",
    "ladrón",
    "ladrona",
    "porquería",
    "porqueria",
    "pésimo",
    "pesimo",
    "horrible",
    "asco",
    "no compren",
    "no compres",
    "no te compren",
    "no me compren",
    "ojalá no",
    "ojala no",
    "quebren",
    "quiebren",
    # Variantes mexicanas comunes en comentarios hostiles
    "rateros",
    "ratero",
    "ratera",
    "engañan",
    "engaño",
    "mentira",
    "mentiras",
    "caro",          # "está muy caro" no es lead, es queja
    "muy caro",
    "carísimo",
    "carisimo",
    "pura basura",
    "no sirve",
    "no sirven",
    "te roban",
    "nos roban",
]

# Intención comercial clara
STRONG_LEAD_KEYWORDS: list[str] = [
    "precio",
    "cuanto",
    "cuánto",
    "kilo",
    "kilos",
    "comprar",
    "compro",
    "pedido",
    "pido",
    "cotiz",
    "cotiza",
    "mayoreo",
    "menudeo",
    "necesito",
    "necesitamos",
    "quiero",
    "quieren",
    "ocupo",
    # Formas coloquiales mexicanas
    "cuánto sale",
    "cuanto sale",
    "a cómo",
    "a como",
    "a cuánto",
    "a cuanto",
    "me das",
    "me puede",
    "nos puede",
    "me vende",
    "nos vende",
    "caja",          # "una caja de nopal"
    "cajas",
    "tonelada",
    "toneladas",
    "ton",
    "arroba",        # unidad de peso usada en campo mexicano
    "arrobas",
    "manojo",        # presentación común de nopal
    "manojos",
    "penca",         # unidad natural del nopal
    "pencas",
    "disponible",    # "¿tienen disponible?"
    "hay",           # "¿hay nopal?" — combinado con PRODUCT_KEYWORDS
    "manejan",       # "¿manejan nopal limpio?"
    "tienen",
]

# Producto — solo cuenta como lead junto con intención de compra
PRODUCT_KEYWORDS: list[str] = [
    "nopal",
    "nopales",
    "tuna",
    "nopalito",      # presentación baby / tierno
    "nopalitos",
    "nopal limpio",  # nopal desespinado, producto de valor agregado
    "verdura",       # genérico pero útil combinado con intención de compra
]

_EXTRA_BUY_INTENT: list[str] = [
    "entrega",
    "envío",
    "envio",
    "surte",
]

BUY_INTENT_KEYWORDS: list[str] = STRONG_LEAD_KEYWORDS + _EXTRA_BUY_INTENT

PROVEEDOR_KEYWORDS: list[str] = [
    "vendo",
    "vendiendo",
    "vende",
    "ofrezco",
    "transporte",
    "distribuyo",
    # Ofertas de insumos o servicios relacionados al campo
    "ofrecemos",
    "contamos con",
    "tenemos disponible",
    "soy proveedor",
    "somos proveedores",
    "fertilizante",
    "fertilizantes",
    "semilla",
    "semillas",
    "maquila",
    "maquinaria",
    "cosecha",       # "ofrezco servicio de cosecha"
    "flete",
    "fletes",
    "camión",
    "camiones",
]

SALUDO_KEYWORDS: list[str] = [
    "hola",
    "buenos días",
    "buenas tardes",
    "buenas noches",
    "buen día",
    "qué tal",
    "que tal",
    # Saludos coloquiales mexicanos
    "buenas",
    "qué onda",
    "que onda",
    "qué hay",
    "que hay",
    "saludos",
    "buen provecho",  # comentarios en posts de recetas con nopal
    "gracias",        # solo si no hay intención de compra — el orden de capas lo maneja
]

OFF_TOPIC_HINTS: list[str] = [
    "xbox",
    "playstation",
    "iphone",
    "crypto",
    "casino",
    "apuesta",
    # Spam común en páginas de Facebook mexicanas
    "préstamo",
    "prestamo",
    "crédito",
    "credito",
    "inversión",
    "inversion",
    "gana dinero",
    "negocio desde casa",
    "trabaja desde casa",
    "multinivel",
    "seguro de vida",
    "bitcoin",
    "dólar",
    "forex",
    "rifa",
    "sorteo",
    "ganador",
]

VALID_CATEGORIES: frozenset[str] = frozenset({"lead", "proveedor", "saludo", "consulta", "spam"})
VALID_ORDER_INTENTS: frozenset[str] = frozenset({"confirmed", "cancelled", "none"})

OPENAI_CLASSIFICATION_PROMPT = (
    "Clasifica este mensaje en: lead, proveedor, saludo, consulta o spam. "
    "Usa lead solo si la persona quiere comprar nopal, cotizar o dar seguimiento a un pedido de nopal. "
    "Usa proveedor cuando la persona ofrece vender, distribuir o surtir productos/servicios. "
    "Usa saludo solo para saludos cortos (ej. hola, buenas tardes). "
    "Usa consulta para peticiones o preguntas generales que no sean compra de nopal. "
    "Usa spam para mensajes hostiles, ofensivos o fuera de tema. "
    "Responde solo con una palabra.\n\n"
)

OPENAI_TOPIC_PROMPT = (
    "Resume en una frase corta (maximo 5 palabras) el tema principal del mensaje del usuario. "
    "No uses puntuacion final ni comillas. "
    "Si el mensaje es solo saludo, responde: consulta general. "
    "Mensaje:\n"
)

OPENAI_ORDER_INTENT_PROMPT = (
    "Analiza si este mensaje confirma o cancela una orden ya abierta. "
    "Responde SOLO con una etiqueta: confirmed, cancelled o none. "
    "Usa confirmed para afirmaciones (ej: si, simon, va, dale, correcto). "
    "Usa cancelled para cancelaciones (ej: ya no, cancela, mejor no). "
    "Si no esta claro, responde none.\n\n"
)
