"""Default LLM model IDs — single place to update per release.

Review against provider docs when cutting a release; model lineups change.
"""

# OpenAI — GPT-4-class small / cheap for disambiguation and extraction
DEFAULT_OPENAI_SMALL = "gpt-4o-mini"

# Anthropic — Claude 4.5 generation (see https://platform.claude.com/docs)
DEFAULT_ANTHROPIC_HAIKU = "claude-haiku-4-5-20251001"
DEFAULT_ANTHROPIC_SONNET = "claude-sonnet-4-5-20250929"


def default_model_for_provider(provider: str) -> str:
    p = (provider or "openai").lower()
    if p == "anthropic":
        return DEFAULT_ANTHROPIC_HAIKU
    return DEFAULT_OPENAI_SMALL
