"""Builds each Messages API request from the chosen settings and the conversation."""
from .config import MODELS, Settings, thinking_can_be_disabled
from .tools import TOOLS

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def thinking_config(settings: Settings) -> dict:
    if settings.thinking:
        return {"type": "adaptive"}
    if not thinking_can_be_disabled(settings.model, settings.effort):
        raise ValueError(f"Thinking cannot be turned off for {settings.model} "
                         f"at effort {settings.effort}")
    return {"type": MODELS[settings.model].thinking_off}


def build_request(settings: Settings, system: str, messages: list) -> dict:
    """`system` already includes the user's system instruction (see prompts.py)."""
    request = {
        "model": settings.model,
        "max_tokens": settings.max_tokens,
        "system": system,
        "messages": messages,
        "tools": [{**tool, "eager_input_streaming": True} for tool in TOOLS],
        "thinking": thinking_config(settings),
        "output_config": {"effort": settings.effort},
        "cache_control": {"type": "ephemeral"},
    }
    if MODELS[settings.model].fallbacks:
        request.update(betas=[FALLBACK_BETA], fallbacks="default")
    return request


def describe_request(request: dict, user_message: str) -> dict:
    """What the UI shows, so each setting can be checked against what was sent."""
    shown = {key: request[key] for key in ("model", "output_config", "thinking", "max_tokens")}
    shown.update(user_message=user_message, system=request["system"],
                 tools=[tool["name"] for tool in request["tools"]])
    if "fallbacks" in request:
        shown["fallbacks"] = request["fallbacks"]
    return shown
