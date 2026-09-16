GREETING_RESPONSE = (
    "¡Hola! Soy Alobot 🌵🤖, asistente virtual. "
    "Cuéntame qué deseas y con gusto te apoyo."
)

ORDER_CONFIRMED_RESPONSE = "Perfecto, tu pedido quedó confirmado. En un momento te contactamos."
ORDER_CANCELLED_RESPONSE = "Entendido, dejamos cancelada esta solicitud."
REPEATED_GREETING_RESPONSE = "¡Hola de nuevo! Soy Alobot 🌵🤖. ¿Qué necesitas hoy?"
HUMAN_CONTACT_BRIDGE_RESPONSE = (
    "En lo que te contacta una persona, te puedo ayudar yo, Alobot 🌵🤖, asistente virtual. "
    "Cuéntame qué necesitas y con gusto te apoyo."
)

GREETING_WITH_HISTORY_TEMPLATE = (
    "¡Hola {name}! Soy Alobot 🌵🤖. "
    "Veo que antes te apoyamos con {quantity} para {city}. "
    "¿Buscas algo similar o te ayudamos con otro tema?"
)
GREETING_WITH_NAME_TEMPLATE = (
    "¡Hola {name}! Soy Alobot 🌵🤖. "
    "Cuéntame qué necesitas y te ayudo a darle seguimiento."
)
GREETING_WITH_HISTORY_NO_NAME_TEMPLATE = (
    "¡Hola! Soy Alobot 🌵🤖. "
    "Veo historial de {quantity} para {city}. "
    "¿Quieres retomar eso o te apoyamos en algo distinto?"
)

ORDER_CONFIRMATION_PROMPT_WITH_SUMMARY = "Antes de cerrar, ¿confirmas tu pedido de {summary}?"
ORDER_CONFIRMATION_PROMPT_DEFAULT = "Antes de cerrar, ¿confirmas tu pedido?"

ORDER_CHANGE_WITH_SUMMARY_TEMPLATE = (
    "Entendido, entonces quieres cambiar {changes} en tu pedido. "
    "¿Te confirmo {summary}?"
)
ORDER_CHANGE_DEFAULT_TEMPLATE = "Entendido, entonces quieres cambiar {changes} en tu pedido."

FALLBACK_HELLO_RESPONSE = "¡Hola! Soy Alobot 🌵🤖. Cuéntame qué deseas y te apoyo."

MISSING_FIELD_LABELS = {
    "nombre": "tu nombre",
    "telefono": "tu teléfono",
    "ciudad": "tu lugar",
    "cantidad": "la cantidad aproximada",
}
CONTACT_MISSING_FIELD_LABELS = {
    "nombre": "tu nombre",
    "telefono": "tu teléfono de contacto",
}
MISSING_FIELDS_PROMPT_SINGLE_TEMPLATE = (
    "Para cotizar nopal y darte seguimiento correctamente, "
    "solo me falta {pending}. ¿Me lo compartes, por favor?"
)
MISSING_FIELDS_PROMPT_PLURAL_TEMPLATE = (
    "Para cotizar nopal y darte seguimiento correctamente, "
    "solo me faltan {pending}. ¿Me los compartes, por favor?"
)

GENERAL_TOPIC_FALLBACK = "tu consulta"
GENERAL_TOPIC_ACK_TEMPLATE = "Perfecto, entonces nos escribes por {topic}."
GENERAL_CONTACT_PROMPT_SINGLE_TEMPLATE = (
    "Para darte seguimiento por ese tema, solo me falta {pending}. "
    "¿Me lo compartes, por favor?"
)
GENERAL_CONTACT_PROMPT_PLURAL_TEMPLATE = (
    "Para darte seguimiento por ese tema, solo me faltan {pending}. "
    "¿Me los compartes, por favor?"
)
GENERAL_CONTACT_READY_TEMPLATE = (
    "Excelente, ya tengo tus datos de contacto para {topic}. "
    "Te damos seguimiento en breve."
)
READY_RESPONSE = "¡Listo! En un momento te contactamos."
INTERACTION_CLOSE_RESPONSE = "Gracias por escribirnos. ¡Listo! En un momento te contactamos."

CLARIFICATION_FALLBACK_MESSAGES = {
    1: "Gracias por escribirnos. Para ayudarte mejor, ¿me cuentas exactamente qué necesitas y me compartes tu nombre y teléfono?",
    2: "Quiero asegurarme de entenderte bien. ¿Qué te gustaría resolver hoy y a qué teléfono te damos seguimiento?",
    3: "Para no equivocarnos, ¿me confirmas en una frase breve qué necesitas y tus datos de contacto?",
}

CLARIFICATION_MAX_WORDS = 40

HUMAN_JOIN_AND = " y "
HUMAN_JOIN_SEPARATOR = ", "

DEFAULT_MISSING_FIELDS: list[str] = ["nombre", "cantidad", "ciudad", "telefono"]
DEFAULT_CONTACT_FIELDS: list[str] = ["nombre", "telefono"]
