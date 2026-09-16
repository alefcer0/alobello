GRAPH_BASE_TEMPLATE = "https://graph.facebook.com/{version}"
GRAPH_MESSAGES_PATH = "/messages"
GRAPH_MESSENGER_ME_PATH = "/me/messages"

META_PAGE_TOKEN_ENV_NAME = "META_PAGE_ACCESS_TOKEN"

HTTP_TIMEOUT_SECONDS = 30

LOG_SEND_MESSAGE_TEMPLATE = "Sending message to %s via %s"
LOG_SKIP_SEND_MISSING_TOKEN = "META_PAGE_ACCESS_TOKEN not set, skipping send"
LOG_SEND_FAILED_TEMPLATE = "Failed to send Meta message: %s"
LOG_WHATSAPP_SENT_TEMPLATE = "WhatsApp message sent to %s"
LOG_MESSENGER_SENT_TEMPLATE = "Messenger message sent to %s"
LOG_RESPONSE_GENERATED_TEMPLATE = "Response generated: '%s' (sent=%s)"

META_RESPONSE_MESSAGE_ID_KEY = "message_id"
META_RESPONSE_MESSAGES_KEY = "messages"
META_RESPONSE_ID_KEY = "id"
