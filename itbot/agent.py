"""The agent loop: call Claude, run the tools it asks for, repeat until it answers."""
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Callable

from . import guardrails
from .config import HELPDESK, MAX_INSTRUCTION_CHARS, Settings, estimate_cost
from .prompts import system_prompt
from .request import build_request, describe_request
from .telemetry import TurnLogger
from .tools import ToolContext, run_tool

USAGE_KEYS = ("input_tokens", "output_tokens",
              "cache_creation_input_tokens", "cache_read_input_tokens")


@dataclass
class TurnResult:
    reply: str = ""
    status: str = "ok"
    tool_calls: list[dict] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: dict.fromkeys(USAGE_KEYS, 0))
    cost_usd: float | None = 0.0
    latency_s: float = 0.0
    request: dict = field(default_factory=dict)  # first request of the turn


class _TurnFailed(Exception):
    def __init__(self, status: str, message: str, detail: str = ""):
        super().__init__(message)
        self.status = status
        self.detail = detail


class SupportAgent:
    """One instance per conversation per signed-in employee."""

    def __init__(self, client, employee: dict, context: ToolContext, settings: Settings,
                 logger: TurnLogger | None = None, today: date | None = None):
        self.client = client
        self.context = context
        self.settings = settings
        self.logger = logger
        instruction = guardrails.redact(settings.instruction.strip()[:MAX_INSTRUCTION_CHARS])[0]
        self.system = system_prompt(employee, today or date.today(), instruction)
        self.messages: list = []
        self._notices: list[str] = []  # app events to tell the model on the next turn

    # -- actions taken by the employee in the UI ---------------------------------

    def confirm_draft(self, draft_id: str) -> dict:
        draft = self.context.drafts[draft_id]
        if draft["state"] != "pending":
            raise ValueError(f"{draft_id} is already {draft['state']}")
        fields = {k: draft[k] for k in ("title", "description", "category", "priority")}
        ticket = self.context.tickets.create(self.context.employee_id, **fields)
        draft.update(state="filed", ticket_id=ticket["id"])
        self._notices.append(f"The employee confirmed {draft_id}. It was filed as {ticket['id']}.")
        return ticket

    def discard_draft(self, draft_id: str) -> None:
        draft = self.context.drafts[draft_id]
        if draft["state"] != "pending":
            raise ValueError(f"{draft_id} is already {draft['state']}")
        draft["state"] = "discarded"
        self._notices.append(f"The employee discarded {draft_id}. Nothing was filed.")

    # -- one chat turn -----------------------------------------------------------

    def send(self, text: str, on_text: Callable[[str], None] | None = None) -> TurnResult:
        started = time.perf_counter()
        result = TurnResult()
        text = text.strip()
        if not text or len(text) > self.settings.max_input_chars:
            result.status = "rejected_input"
            result.reply = f"Please send a message of 1 to {self.settings.max_input_chars} characters."
            return result

        safe_text, kinds = guardrails.redact(text)
        result.flags += [f"redacted: {k}" for k in kinds]

        # History is append-only. A failed turn is cut off the end, which leaves
        # everything before it unchanged.
        checkpoint = len(self.messages)
        escalated_before = len(self.context.escalations)
        drafts_before = set(self.context.drafts)
        self.messages.append({"role": "user", "content": safe_text})
        if self._notices:
            # Operator channel, kept apart from the employee's own words.
            self.messages.append({"role": "system", "content": " ".join(self._notices)})

        try:
            self._run_loop(result, on_text)
            self._notices = []
        except _TurnFailed as failure:
            del self.messages[checkpoint:]
            result.status = failure.status
            result.reply = f"{failure} {HELPDESK}"
            if failure.detail:  # shown in the trace and logged, for whoever is debugging
                result.flags.append(failure.detail)
            for draft_id in set(self.context.drafts) - drafts_before:
                del self.context.drafts[draft_id]
            for ticket in self.context.escalations[escalated_before:]:
                # The escalation really happened, so say so and remember it.
                note = f"Escalation {ticket['id']} was filed."
                result.reply += f" {note}"
                self._notices.append(note)

        result.reply = guardrails.sanitize_output(result.reply)
        cited = set(re.findall(r"\bKB-\d{3}\b", result.reply))
        result.sources = sorted(cited & self.context.retrieved)
        bad = guardrails.unknown_citations(result.reply, self.context.retrieved)
        if result.status == "ok" and bad:
            result.flags.append(f"cited but never retrieved: {', '.join(bad)}")
        result.latency_s = round(time.perf_counter() - started, 2)
        if self.logger:
            self.logger.log({"employee": self.context.employee_id, "model": self.settings.model,
                             "effort": self.settings.effort, "thinking": self.settings.thinking,
                             "user_text": safe_text,
                             **{k: v for k, v in asdict(result).items() if k != "request"}})
        return result

    def _run_loop(self, result: TurnResult, on_text) -> None:
        shown = ""
        for _ in range(self.settings.max_tool_rounds):
            request = build_request(self.settings, self.system, self.messages)
            if not result.request:
                user_message = next(m["content"] for m in reversed(self.messages)
                                    if m["role"] == "user" and isinstance(m["content"], str))
                result.request = describe_request(request, user_message)
            response, shown = self._call_model(request, shown, on_text)
            self._count(response, result)

            # Check why the model stopped before trusting the content.
            if response.stop_reason == "refusal":
                raise _TurnFailed("refusal", "I can't help with that request.")
            if response.stop_reason == "max_tokens":
                raise _TurnFailed("truncated", "My answer was cut off. Please ask in smaller parts.")

            content = replayable(response.content)
            if len(content) != len(response.content) or any(b.type == "fallback" for b in content):
                note = f"a fallback model answered after a refusal: {response.model}"
                if note not in result.flags:
                    result.flags.append(note)
            self.messages.append({"role": "assistant", "content": content})
            calls = [b for b in content if b.type == "tool_use"]

            if response.stop_reason == "tool_use" and calls:
                outputs = []
                for call in calls:
                    try:
                        output, is_error = run_tool(call.name, call.input, self.context)
                    except (OSError, ValueError) as error:
                        output, is_error = f"The tool failed: {type(error).__name__}", True
                    safe_args = guardrails.redact(json.dumps(call.input))[0]
                    result.tool_calls.append(
                        {"name": call.name, "input": json.loads(safe_args), "is_error": is_error})
                    outputs.append({"type": "tool_result", "tool_use_id": call.id,
                                    "content": output, "is_error": is_error})
                # All results from one reply go back in a single message.
                self.messages.append({"role": "user", "content": outputs})
                continue

            if response.stop_reason != "end_turn":
                raise _TurnFailed("unexpected_stop", "Something went wrong.")
            result.reply = shown.strip()
            if not result.reply:
                raise _TurnFailed("empty_reply", "I didn't produce an answer. Please try again.")
            return
        raise _TurnFailed("tool_limit", "I couldn't finish that within my step limit.")

    def _call_model(self, request: dict, shown: str, on_text):
        separator = "\n\n" if shown else ""
        # Beta parameters such as `fallbacks` live on the beta endpoint.
        api = self.client.beta.messages if "betas" in request else self.client.messages
        try:
            with api.stream(**request) as stream:
                for chunk in stream.text_stream:
                    shown += separator + chunk
                    separator = ""
                    if on_text:
                        on_text(guardrails.sanitize_output(shown))
                return stream.get_final_message(), shown
        except ValueError as error:  # unparseable streamed tool input
            raise _TurnFailed("bad_tool_input", "Something went wrong. Please try again.") from error
        except Exception as error:
            raise _TurnFailed(*describe_api_error(error), api_error_detail(error)) from error

    def _count(self, response, result: TurnResult) -> None:
        usage = {k: getattr(response.usage, k, 0) or 0 for k in USAGE_KEYS}
        for key, value in usage.items():
            result.usage[key] += value
        # Priced by the model that answered, which differs if a fallback ran.
        cost = estimate_cost(getattr(response, "model", None) or self.settings.model, usage)
        result.cost_usd = None if cost is None or result.cost_usd is None else result.cost_usd + cost


def replayable(content: list) -> list:
    """What to keep from a reply. If a fallback model took over part-way, the declined
    model's thinking and tool calls before the switch are neither run nor sent back."""
    switches = [i for i, block in enumerate(content) if block.type == "fallback"]
    if not switches:
        return list(content)
    dropped = ("thinking", "redacted_thinking", "tool_use")
    return [b for i, b in enumerate(content) if i >= switches[-1] or b.type not in dropped]


def api_error_detail(error: Exception) -> str:
    """The API's own explanation and request ID, if the error carries them."""
    body = getattr(error, "body", None)
    inner = body.get("error") if isinstance(body, dict) else None
    message = inner.get("message") if isinstance(inner, dict) else None
    if not message:
        return ""
    request_id = getattr(error, "request_id", None)
    suffix = f" (request ID {request_id})" if request_id else ""
    return f"API said: {guardrails.redact(str(message))[0]}{suffix}"


def describe_api_error(error: Exception) -> tuple[str, str]:
    """Turn an SDK error into (status, message). Re-raises anything that is not one."""
    try:
        import anthropic
    except ImportError:
        raise error
    # Most specific first.
    if isinstance(error, anthropic.AuthenticationError):
        return "auth_error", "The API key was rejected. Check ANTHROPIC_API_KEY in .env."
    if isinstance(error, anthropic.PermissionDeniedError):
        return "permission_error", "The API key does not have access to this model."
    if isinstance(error, anthropic.NotFoundError):
        return "model_not_found", "The configured model was not found. Check ITBOT_MODEL."
    if isinstance(error, anthropic.RateLimitError):
        return "rate_limited", "Too many requests right now. Wait a minute and retry."
    if isinstance(error, anthropic.APIStatusError):
        return "api_error", f"The AI service returned an error (HTTP {error.status_code})."
    if isinstance(error, anthropic.APIConnectionError):
        return "connection_error", "I couldn't reach the AI service. Check your connection."
    raise error
