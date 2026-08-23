from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


class SignalState:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.sent: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.sent = set(raw.get("sent_signal_ids", []))

    def contains(self, signal_id: str) -> bool:
        return signal_id in self.sent

    def mark_sent(self, signal_id: str) -> None:
        self.sent.add(signal_id)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now(UTC).isoformat(),
            "sent_signal_ids": sorted(self.sent),
        }
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_heartbeat(path: str | Path, *, status: str, details: dict | None = None) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ran_at": datetime.now(UTC).isoformat(),
        "status": status,
        "details": details or {},
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

