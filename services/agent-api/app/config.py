"""AI Site System — Agent API configuration."""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://app_user:password@postgres:5432/ai_site_system"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Security
    agent_api_secret: str = "changeme"

    # LLM
    openai_api_key: str = ""
    openai_base_url: str = "https://openrouter.ai/api/v1"
    anthropic_api_key: str = ""
    default_llm_provider: str = "openai"
    default_llm_model: str = "openai/gpt-4o"

    # Paths
    generated_sites_path: str = "/data/generated-sites"
    artifacts_path: str = "/data/artifacts"

    # URLs
    preview_base_url: str = "http://localhost/preview"
    site_base_url: str = "https://agent.santinoventre.com"

    # Google Sheets
    google_sheets_credentials_path: str = "/secrets/gsheets-credentials.json"
    google_drive_folder_id: str = ""

    # Images
    unsplash_access_key: str = ""
    pexels_api_key: str = ""
    replicate_api_token: str = ""
    ai_images_enabled: bool = False

    # CORS — comma-separated list of allowed origins (e.g. "https://agent.example.com,https://sites.example.com")
    cors_allowed_origins: str = ""

    # Public sites domain (used in preview links / seo)
    domain: str = "localhost"

    # Quality loop
    quality_score_threshold: int = 80
    quality_max_iterations: int = 2

    # QA runner
    qa_runner_url: str = "http://qa-runner:8001"
    qa_enabled: bool = True

    # Telegram proactive notifier
    telegram_bot_url: str = "http://telegram-bot:8080"
    telegram_notify_enabled: bool = True

    # Observability
    log_level: str = "INFO"
    log_json: bool = True
    rate_limit_default: str = "60/minute"
    rate_limit_public: str = "60/minute"

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
