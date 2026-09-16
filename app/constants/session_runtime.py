JSON_REGEX_OBJECT = r"\{.*\}"
WHITESPACE_REGEX = r"\s+"
WORD_BOUNDARY_TEMPLATE = r"\b{token}\b"
QUANTITY_PARSE_REGEX = r"(\d+(?:\.\d+)?)\s*([A-Za-z]+)?"

MESSAGE_CATEGORY_SALUDO = "saludo"
MESSAGE_CATEGORY_LEAD = "lead"

ORDER_INTENT_CONFIRMED = "confirmed"
ORDER_INTENT_CANCELLED = "cancelled"

LOG_GET_OR_CREATE_SESSION_TEMPLATE = "Getting or creating session for sender=%s platform=%s"
LOG_NEW_CONVERSATION_TEMPLATE = "New conversation created: %s"
LOG_CONVERSATION_RENEWED_TEMPLATE = "Conversation renewed after expiry: %s"
LOG_ACTIVE_CONVERSATION_TEMPLATE = "Active conversation found: %s"
LOG_MERGE_DATA_TEMPLATE = "Merging extracted data into conversation %s"
LOG_MERGED_SESSION_TEMPLATE = "Merged session data: %s (source=%s)"
LOG_MESSAGE_SAVED_TEMPLATE = "Message saved: %s (direction=%s categoria=%s)"

ERROR_CONVERSATION_NOT_FOUND_TEMPLATE = "Conversation not found: {conversation_id}"

SQL_STATUS_OPEN = "open"
SQL_STATUS_CLOSED = "closed"
SOURCE_FIRST_MESSAGE = "primer_mensaje"
SOURCE_RESPONSE = "respuesta"
