from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"

    # Sentry — optional. Leave blank locally if you don't want every dev-time
    # exception showing up in the dashboard; set it in staging/prod.
    sentry_dsn: str = ""

    # Supabase — required, checked explicitly at startup (see check_required_settings)
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_jwt_secret: str = ""
    supabase_anon_key: str = ""  # reference only, backend doesn't use it

    # Google OAuth — required once you build the Calendar/Drive connect flow,
    # optional for everything before that (Task CRUD, etc. don't need it)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""

    # Gemini — required once you build capture/AI endpoints
    gemini_api_key: str = ""

    # Firebase — required once you build the reminder scheduler
    fcm_service_account_path: str = "./firebase-service-account.json"
    fcm_project_id: str = ""


@lru_cache
def get_settings() -> Settings:
    # lru_cache means .env is only read once per process, not per request.
    return Settings()


# Vars that MUST be set for the app to run at all, right now, at this
# stage of the build. As you build more of Step 5, move entries here
# once that feature actually starts depending on them (e.g. add
# "gemini_api_key" once the capture endpoints exist).
REQUIRED_NOW = ["supabase_url", "supabase_service_key", "supabase_jwt_secret"]


def check_required_settings() -> list[str]:
    """Returns a list of required settings that are still blank. Call this
    at startup (see main.py) so a missing .env value fails immediately
    with a clear message instead of a confusing error three layers deep
    the first time that route gets hit."""
    settings = get_settings()
    return [name for name in REQUIRED_NOW if not getattr(settings, name)]
