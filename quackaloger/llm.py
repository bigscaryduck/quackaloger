"""Provider-agnostic structured extraction via tool/function calling."""

from __future__ import annotations

import json
from typing import Any, Optional, Protocol, Sequence

TOOL_NAME = "submit_extraction"


class ExtractError(Exception):
    """Raised when the model did not return a valid tool payload."""


class ExtractClient(Protocol):
    def extract(
        self,
        messages: Sequence[dict[str, Any]],
        input_schema: dict[str, Any],
        *,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        ...


def _validate_required_keys(obj: dict[str, Any], required: list[str]) -> None:
    for k in required:
        if k not in obj:
            raise ExtractError(f"Missing required key {k!r} in extraction result")


class OpenAIExtractClient:
    """OpenAI Chat Completions with a single forced function tool."""

    def __init__(self, api_key: str, model: str):
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self._model = model

    def extract(
        self,
        messages: Sequence[dict[str, Any]],
        input_schema: dict[str, Any],
        *,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": TOOL_NAME,
                    "description": "Submit the structured extraction result.",
                    "parameters": input_schema,
                },
            }
        ]
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=list(messages),
            tools=tools,
            tool_choice={"type": "function", "function": {"name": TOOL_NAME}},
            parallel_tool_calls=False,
            temperature=temperature,
        )
        msg = resp.choices[0].message
        calls = getattr(msg, "tool_calls", None) or []
        if not calls:
            raise ExtractError("OpenAI response had no tool_calls")
        raw = calls[0].function.arguments
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise ExtractError(f"Invalid JSON in tool arguments: {e}") from e


class AnthropicExtractClient:
    """Anthropic Messages API with a single input_schema tool."""

    def __init__(self, api_key: str, model: str):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def extract(
        self,
        messages: Sequence[dict[str, Any]],
        input_schema: dict[str, Any],
        *,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        tools = [
            {
                "name": TOOL_NAME,
                "description": "Submit the structured extraction result.",
                "input_schema": input_schema,
            }
        ]
        # Anthropic expects system as top-level; merge simple user/system from messages
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        other = [m for m in messages if m.get("role") != "system"]
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 2048,
            "temperature": temperature,
            "tools": tools,
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
            "messages": list(other),
        }
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)
        resp = self._client.messages.create(**kwargs)
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "tool_use" and getattr(block, "name", "") == TOOL_NAME:
                return dict(block.input)
        raise ExtractError("Anthropic response had no matching tool_use block")


def build_extract_client(
    provider: str,
    *,
    openai_key: str = "",
    openai_model: str = "",
    anthropic_key: str = "",
    anthropic_model: str = "",
) -> Optional[ExtractClient]:
    """Return a client if credentials allow, else None."""
    from quackaloger import llm_models

    p = (provider or "openai").lower()
    if p == "anthropic":
        if not anthropic_key:
            return None
        model = anthropic_model or llm_models.DEFAULT_ANTHROPIC_HAIKU
        return AnthropicExtractClient(anthropic_key, model)
    if openai_key:
        model = openai_model or llm_models.DEFAULT_OPENAI_SMALL
        return OpenAIExtractClient(openai_key, model)
    return None
