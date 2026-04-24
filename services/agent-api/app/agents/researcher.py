"""Researcher agent — optional competitive and design research."""

import logging
from app.services.llm_client import call_llm

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a web design and content researcher. Given a project specification, research and return structured findings.

Output format:
{
  "competitors": [
    {"name": "string", "url": "string", "strengths": ["..."], "weaknesses": ["..."]}
  ],
  "design_references": [
    {"description": "string", "style": "string", "relevance": "string"}
  ],
  "content_ideas": ["list of content suggestions"],
  "seo_keywords": ["relevant keywords for the site"],
  "recommendations": ["actionable recommendations"]
}

Focus on practical, actionable insights. Keep it concise."""


async def run_researcher(project_spec: dict) -> dict:
    """Run the researcher agent. Returns research_report."""
    logger.info("Running researcher agent")

    user_prompt = f"Project specification:\n{project_spec}"

    result = await call_llm(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        response_format="json",
        temperature=0.5,
        max_tokens=4096,
    )

    logger.info(f"Researcher found {len(result.get('recommendations', []))} recommendations")
    return result
