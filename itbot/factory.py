"""Wires the pieces together. Used by the UI and the evals."""
import json
from pathlib import Path

from .agent import SupportAgent
from .config import ROOT, Settings
from .knowledge import KnowledgeBase
from .telemetry import TurnLogger
from .tickets import TicketStore
from .tools import ToolContext


def load_employees() -> dict[str, dict]:
    people = json.loads((ROOT / "data" / "employees.json").read_text())
    return {p["id"]: p for p in people}


def build_agent(client, employee_id: str, settings: Settings,
                runtime: Path | None = None, today=None) -> SupportAgent:
    """`runtime` is where tickets and logs are written (the evals use a temp folder)."""
    runtime = runtime or ROOT / "runtime"
    context = ToolContext(
        employee_id=employee_id,
        kb=KnowledgeBase(ROOT / "kb"),
        tickets=TicketStore(runtime / "tickets.json", ROOT / "data" / "seed_tickets.json"),
        status_file=ROOT / "data" / "service_status.json")
    return SupportAgent(client, load_employees()[employee_id], context, settings,
                        TurnLogger(runtime / "turns.jsonl"), today)
