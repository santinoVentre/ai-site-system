"""Telegram bot configuration."""

from pydantic_settings import BaseSettings
from functools import lru_cache


class BotSettings(BaseSettings):
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    telegram_admin_chat_id: str = ""
    agent_api_url: str = "http://agent-api:8000"
    agent_api_secret: str = ""
    n8n_webhook_url: str = "http://n8n:5678"
    redis_url: str = "redis://redis:6379/2"

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_bot_settings() -> BotSettings:
    return BotSettings()
