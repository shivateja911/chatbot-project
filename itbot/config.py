"""Settings and the model catalog. Defaults can be overridden with ITBOT_* values in .env."""
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EFFORTS = ["low", "medium", "high", "xhigh", "max"]
# On the models that allow thinking to be turned off, it only works up to this effort.
EFFORTS_ALLOWING_THINKING_OFF = ["low", "medium", "high"]
MAX_INSTRUCTION_CHARS = 2000


@dataclass(frozen=True)
class ModelInfo:
    label: str
    prices: tuple[float, float, float]  # US$ per million: input, output, cache read
    thinking_off: str | None  # `thinking.type` that turns thinking off; None = cannot
    fallbacks: bool  # request the server-side refusal fallback
    long_prices: tuple[float, float, float] | None = None  # prompts over 100K tokens


# As of November 2026.
MODELS = {
    "claude-haiku-5-5": ModelInfo("Claude Haiku 5.5", (0.10, 0.50, 0.01), "disabled", False,
                                  long_prices=(0.50, 2.50, 0.05)),
    "claude-sonnet-5-5": ModelInfo("Claude Sonnet 5.5", (2.00, 10.00, 0.10), "between_tools", True),
    "claude-opus-5-5": ModelInfo("Claude Opus 5.5", (4.00, 20.00, 0.20), None, True),
}

HELPDESK = "IT helpdesk: extension 4357. Suspected security incidents: IT security, extension 5555."


@dataclass(frozen=True)
class Settings:
    model: str = "claude-haiku-5-5"
    effort: str = "high"
    thinking: bool = True
    instruction: str = ""
    # A ceiling, not a target. Thinking counts toward it, so leave room at high effort.
    max_tokens: int = 32000
    max_tool_rounds: int = 6
    max_input_chars: int = 4000


def thinking_can_be_disabled(model: str, effort: str) -> bool:
    return MODELS[model].thinking_off is not None and effort in EFFORTS_ALLOWING_THINKING_OFF


def load_settings() -> Settings:
    model = os.getenv("ITBOT_MODEL", Settings.model)
    effort = os.getenv("ITBOT_EFFORT", Settings.effort)
    if model not in MODELS:
        raise ValueError(f"ITBOT_MODEL must be one of {list(MODELS)}, not {model!r}")
    if effort not in EFFORTS:
        raise ValueError(f"ITBOT_EFFORT must be one of {EFFORTS}, not {effort!r}")
    return Settings(model=model, effort=effort)


def estimate_cost(model: str, usage: dict) -> float | None:
    """Estimated US$ for ONE request. None if the model has no price listed."""
    info = MODELS.get(model)
    if info is None:
        return None
    plain = usage.get("input_tokens", 0)
    write = usage.get("cache_creation_input_tokens", 0)
    read = usage.get("cache_read_input_tokens", 0)
    long_prompt = info.long_prices and plain + write + read > 100_000
    rate_in, rate_out, rate_read = info.long_prices if long_prompt else info.prices
    total = ((plain + 1.25 * write) * rate_in + read * rate_read
             + usage.get("output_tokens", 0) * rate_out)
    return total / 1_000_000
