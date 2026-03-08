from __future__ import annotations

"""
Log Agent — writes every GOTO command and agent event to a session
log file. Tracks session statistics. Strips frame_b64 to keep files small.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is on sys.path so `utils` is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.base_agent import BaseAgent


class LogAgent(BaseAgent):
    AGENT_NAME = "log-agent"

    def __init__(self):
        super().__init__()
        self.log_file = self.args.log_file
        self.objects_seen = []
        self.interaction_count = 0

    def add_args(self, parser):
        parser.add_argument(
            "--log-file", type=str, default="reachy_sessions.jsonl",
            help="Path to session log file (default: reachy_sessions.jsonl)"
        )

    def on_start(self):
        # Also subscribe to all agent events
        self.bus.subscribe("reachy/events/#", self._handle_event)
        print(f"[{self.AGENT_NAME}] Logging to {self.log_file}")

    def handle_goto(self, payload: dict):
        self.interaction_count += 1
        obj = payload.get("object", "unknown")
        self.objects_seen.append(obj)

        # Strip the large frame_b64 before logging
        log_entry = {k: v for k, v in payload.items() if k != "frame_b64"}
        log_entry["_event_type"] = "goto"
        log_entry["_session_stats"] = {
            "interaction_count": self.interaction_count,
            "unique_objects": len(set(self.objects_seen)),
            "objects_seen": list(set(self.objects_seen)),
        }

        self._write(log_entry)
        print(
            f"[{self.AGENT_NAME}] Logged GOTO #{payload.get('interaction_id', '?')}: "
            f"{obj} (total: {self.interaction_count} interactions, "
            f"{len(set(self.objects_seen))} unique objects)"
        )

    def _handle_event(self, topic: str, payload: dict):
        log_entry = {k: v for k, v in payload.items() if k != "frame_b64"}
        log_entry["_event_type"] = "agent_event"
        log_entry["_topic"] = topic
        self._write(log_entry)

    def _write(self, entry: dict):
        entry["_logged_at"] = datetime.now().isoformat()
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    LogAgent().run()
