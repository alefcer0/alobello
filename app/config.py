from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    meta_verify_token: str = ""
    meta_app_secret: str = ""
    meta_page_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    openai_api_key: str = ""
    database_url: str = "postgresql://postgres:postgres@db:5432/nopal_leads"
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "nopal_leads"
    session_expiry_hours: int = 48
    meta_graph_api_version: str = "v21.0"


settings = Settings()
