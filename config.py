from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"

    supabase_url: str
    supabase_service_key: str
    supabase_jwt_secret: str

    google_client_id: str = ""
    google_client_secret: str = ""

    gemini_api_key: str = ""

    fcm_service_account_path: str = "./firebase-service-account.json"


@lru_cache
def get_settings() -> Settings:
    # lru_cache means .env is only read once per process, not per request.
    return Settings()
