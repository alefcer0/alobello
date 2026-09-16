from pydantic_settings import BaseSettings, SettingsConfigDict

from app.constants.app_meta import (
    DEFAULT_BOT_OUTBOUND_EVENT_TTL_SECONDS,
    DEFAULT_DATABASE_URL,
    DEFAULT_HANDOFF_IDLE_TIMEOUT_MINUTES,
    DEFAULT_JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
    DEFAULT_JWT_ALGORITHM,
    DEFAULT_MAX_CLARIFICATION_ATTEMPTS,
    DEFAULT_MAX_TOPIC_CHANGES,
    DEFAULT_MESSAGE_BURST_INACTIVITY_SECONDS,
    DEFAULT_MESSAGE_BURST_MAX_MESSAGES,
    DEFAULT_MESSAGE_BURST_MAX_WAIT_SECONDS,
    DEFAULT_META_GRAPH_API_VERSION,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_POSTGRES_DB,
    DEFAULT_POSTGRES_PASSWORD,
    DEFAULT_POSTGRES_USER,
    DEFAULT_SESSION_EXPIRY_HOURS,
    DEFAULT_SESSION_IDLE_TIMEOUT_MINUTES,
    DEFAULT_SESSION_MAX_DURATION_MINUTES,
)
from app.constants.schema import SETTINGS_ENV_FILE, SETTINGS_EXTRA_IGNORE


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=SETTINGS_ENV_FILE, extra=SETTINGS_EXTRA_IGNORE)

    meta_verify_token: str = ""
    meta_app_secret: str = ""
    meta_page_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    openai_api_key: str = ""
    openai_model: str = DEFAULT_OPENAI_MODEL
    database_url: str = DEFAULT_DATABASE_URL
    postgres_user: str = DEFAULT_POSTGRES_USER
    postgres_password: str = DEFAULT_POSTGRES_PASSWORD
    postgres_db: str = DEFAULT_POSTGRES_DB
    # Legacy fallback; prefer minute-based settings below.
    session_expiry_hours: int = DEFAULT_SESSION_EXPIRY_HOURS
    session_idle_timeout_minutes: int = DEFAULT_SESSION_IDLE_TIMEOUT_MINUTES
    session_max_duration_minutes: int = DEFAULT_SESSION_MAX_DURATION_MINUTES
    message_burst_inactivity_seconds: float = DEFAULT_MESSAGE_BURST_INACTIVITY_SECONDS
    message_burst_max_wait_seconds: float = DEFAULT_MESSAGE_BURST_MAX_WAIT_SECONDS
    message_burst_max_messages: int = DEFAULT_MESSAGE_BURST_MAX_MESSAGES
    handoff_idle_timeout_minutes: int = DEFAULT_HANDOFF_IDLE_TIMEOUT_MINUTES
    max_clarification_attempts: int = DEFAULT_MAX_CLARIFICATION_ATTEMPTS
    max_topic_changes: int = DEFAULT_MAX_TOPIC_CHANGES
    bot_outbound_event_ttl_seconds: float = DEFAULT_BOT_OUTBOUND_EVENT_TTL_SECONDS
    meta_graph_api_version: str = DEFAULT_META_GRAPH_API_VERSION
    jwt_secret_key: str = ""
    jwt_algorithm: str = DEFAULT_JWT_ALGORITHM
    jwt_access_token_expire_minutes: int = DEFAULT_JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    superuser_username: str = ""
    superuser_password: str = ""


settings = Settings()
