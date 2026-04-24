"""API authentication and authorization dependencies."""

from fastapi import Header, HTTPException
from app.config import get_settings


async def verify_api_secret(x_api_secret: str = Header(..., alias="X-API-Secret")):
    """Verify the API secret header for internal service-to-service calls."""
    settings = get_settings()
    if x_api_secret != settings.agent_api_secret:
        raise HTTPException(status_code=401, detail="Invalid API secret")
    return True
