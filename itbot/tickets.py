"""Mock ticket system stored in a local JSON file."""
import json
import os
import threading
from datetime import datetime
from pathlib import Path


class TicketStore:
    def __init__(self, path: Path, seed: Path | None = None):
        self.path = path
        self._lock = threading.Lock()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            self._write(json.loads(seed.read_text()) if seed else [])

    def _read(self) -> list[dict]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, tickets: list[dict]) -> None:
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(tickets, indent=2), encoding="utf-8")
        os.replace(temp, self.path)

    def for_owner(self, owner: str) -> list[dict]:
        return [t for t in self._read() if t["owner"] == owner]

    def get(self, owner: str, ticket_id: str) -> dict | None:
        wanted = ticket_id.strip().upper()
        return next((t for t in self.for_owner(owner) if t["id"] == wanted), None)

    def create(self, owner: str, *, title: str, description: str, category: str,
               priority: str, kind: str = "ticket") -> dict:
        with self._lock:
            tickets = self._read()
            number = max((int(t["id"].split("-")[1]) for t in tickets), default=1000) + 1
            ticket = {"id": f"LH-{number}", "owner": owner, "title": title,
                      "description": description, "category": category, "priority": priority,
                      "status": "open", "kind": kind,
                      "created": datetime.now().isoformat(timespec="seconds"), "notes": []}
            self._write(tickets + [ticket])
            return ticket
