"""Tool definitions and the code that runs them.

No tool takes an employee ID: the application supplies identity, so the model
cannot be talked into reading someone else's records.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

from .guardrails import redact
from .knowledge import KnowledgeBase
from .tickets import TicketStore

CATEGORIES = ["hardware", "software", "network", "access", "other"]
PRIORITIES = ["low", "normal", "high"]
ESCALATION_REASONS = ["security_incident", "lost_or_stolen_device", "outage",
                      "mfa_or_account_reset", "user_asked_for_human", "other"]


def _tool(name, description, properties):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": properties,
                             "required": list(properties), "additionalProperties": False}}


_text = {"type": "string"}

TOOLS = [
    _tool("search_knowledge_base",
          "Search the approved IT help articles. Use this before giving any how-to steps "
          "or stating any IT policy. Returns up to three full articles with their IDs.",
          {"query": {**_text, "description": "Keywords describing the problem"}}),
    _tool("check_service_status",
          "Get the current status of company IT services. Use this when something shared "
          "(email, VPN, printing, Wi-Fi, sign-in) is not working.", {}),
    _tool("list_my_tickets", "List the signed-in employee's own tickets and their status.", {}),
    _tool("get_ticket", "Get one of the signed-in employee's own tickets, including notes.",
          {"ticket_id": {**_text, "description": "For example LH-1001"}}),
    _tool("draft_ticket",
          "Prepare a ticket for the employee to review. This does NOT file it: the employee "
          "files it with the Confirm button. Use when self-help did not solve the problem "
          "or the request needs IT staff.",
          {"title": _text,
           "description": {**_text, "description": "Symptoms, what was tried, device and any error"},
           "category": {**_text, "enum": CATEGORIES},
           "priority": {**_text, "enum": PRIORITIES}}),
    _tool("escalate_to_human",
          "Hand off to a person immediately, with no confirmation step. Use right away for "
          "suspected security incidents, lost or stolen devices, MFA resets for a lost phone, "
          "widespread outages, or when the employee asks for a human.",
          {"reason": {**_text, "enum": ESCALATION_REASONS},
           "summary": {**_text, "description": "What happened and what the person needs"}}),
]


@dataclass
class ToolContext:
    employee_id: str
    kb: KnowledgeBase
    tickets: TicketStore
    status_file: Path
    drafts: dict[str, dict] = field(default_factory=dict)
    escalations: list[dict] = field(default_factory=list)
    retrieved: set[str] = field(default_factory=set)


def _validate(name: str, args: dict) -> str | None:
    schema = next((t["input_schema"] for t in TOOLS if t["name"] == name), None)
    if schema is None:
        return f"Unknown tool: {name}"
    if not isinstance(args, dict) or set(args) != set(schema["properties"]):
        return f"Expected exactly these fields: {sorted(schema['properties'])}"
    for key, rule in schema["properties"].items():
        if not isinstance(args[key], str) or not args[key].strip():
            return f"'{key}' must be a non-empty string"
        if "enum" in rule and args[key] not in rule["enum"]:
            return f"'{key}' must be one of {rule['enum']}"
    return None


def run_tool(name: str, args: dict, ctx: ToolContext) -> tuple[str, bool]:
    """Run a tool. Returns (result text, is_error)."""
    problem = _validate(name, args)
    if problem:
        return problem, True
    args = {key: redact(value)[0] for key, value in args.items()}

    if name == "search_knowledge_base":
        hits = ctx.kb.search(args["query"])
        if not hits:
            return "No matching articles. Do not guess; offer a ticket or a handoff.", False
        ctx.retrieved.update(a.id for a in hits)
        return "\n\n".join(f"<article id=\"{a.id}\">\n{a.body}</article>" for a in hits), False
    if name == "check_service_status":
        return ctx.status_file.read_text(encoding="utf-8"), False
    if name == "list_my_tickets":
        keys = ("id", "title", "status", "priority", "kind", "created")
        return json.dumps([{k: t[k] for k in keys} for t in ctx.tickets.for_owner(ctx.employee_id)]), False
    if name == "get_ticket":
        ticket = ctx.tickets.get(ctx.employee_id, args["ticket_id"])
        if ticket is None:  # Same answer for "missing" and "someone else's".
            return "No ticket with that ID on this employee's account.", True
        return json.dumps(ticket), False
    if name == "draft_ticket":
        draft_id = f"draft-{len(ctx.drafts) + 1}"
        ctx.drafts[draft_id] = {**args, "state": "pending"}
        return (f"{draft_id} is shown to the employee. It is NOT filed yet. Ask them to "
                "review it and press Confirm. Do not give a ticket number."), False

    urgent = args["reason"] in ("security_incident", "lost_or_stolen_device", "outage")
    ticket = ctx.tickets.create(
        ctx.employee_id, title=f"Escalation: {args['reason'].replace('_', ' ')}",
        description=args["summary"], category="other",
        priority="high" if urgent else "normal", kind="escalation")
    ctx.escalations.append(ticket)
    return f"Escalation {ticket['id']} filed, priority {ticket['priority']}. A person will follow up.", False
