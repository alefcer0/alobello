UTF8_ENCODING = "utf-8"
UNKNOWN_ENTRY_ID = "unknown"

LOG_MISSING_SIGNATURE_OR_SECRET = "Missing signature or app secret"
LOG_INVALID_SIGNATURE = "Invalid Meta webhook signature"
LOG_PARSED_MESSAGES_TEMPLATE = "Parsed %d messages from webhook payload"

DUPLICATE_EVENT_SELECT_SQL = "SELECT 1 FROM processed_events WHERE event_id = $1"
MARK_PROCESSED_INSERT_SQL = "INSERT INTO processed_events (event_id) VALUES ($1) ON CONFLICT DO NOTHING"

EVENT_TIMESTAMP_FALLBACK = "na"
