from __future__ import annotations

"""
Base class for all sub-agents.

Handles CLI args, MQTT/HTTP connection, message routing,
and status/event publishing. Subclasses override handle_goto()
and optionally handle_system().
"""

import argparse
import json
import time
from datetime import datetime, timezone

from utils.command_bus import CommandBus


class BaseAgent:
    AGENT_NAME = "base"  # Override in subclass

    def __init__(self):
        self.args = self._parse_args()
        self.bus = CommandBus(
            mqtt_broker=self.args.broker,
            mqtt_port=self.args.mqtt_port,
        )
        self._interaction_count = 0
        self._current_demo_context: str | None = None

    def _parse_args(self):
        parser = argparse.ArgumentParser(
            description=f"Reachy {self.AGENT_NAME} agent"
        )
        parser.add_argument(
            "--broker", type=str, default="localhost",
            help="MQTT broker hostname (default: localhost)"
        )
        parser.add_argument(
            "--mqtt-port", type=int, default=1883,
            help="MQTT broker port (default: 1883)"
        )
        parser.add_argument(
            "--http-port", type=int, default=None,
            help="HTTP fallback server port (optional)"
        )
        parser.add_argument(
            "--model", type=str, default="claude-sonnet-4-20250514",
            help="Claude model for agent brain (default: claude-sonnet-4-20250514)"
        )
        parser.add_argument(
            "--no-claude", action="store_true",
            help="Disable Claude brain, use deterministic fallback only"
        )
        self.add_args(parser)
        return parser.parse_args()

    def add_args(self, parser):
        """Override to add agent-specific CLI arguments."""
        pass

    def handle_goto(self, payload: dict):
        """Override to handle GOTO commands. Called for each reachy/commands/goto message."""
        pass

    def handle_system(self, payload: dict):
        """Override to handle system commands (shutdown, pause, resume)."""
        cmd_type = payload.get("type")
        if cmd_type == "shutdown":
            print(f"[{self.AGENT_NAME}] Shutdown requested.")
            raise SystemExit(0)

    def publish_status(self, data: dict):
        """Publish a status heartbeat."""
        data["agent"] = self.AGENT_NAME
        data["timestamp"] = datetime.now(timezone.utc).isoformat()
        self.bus.publish(f"reachy/status/{self.AGENT_NAME}", data)

    def publish_event(self, data: dict):
        """Publish a completed-action event."""
        data["agent"] = self.AGENT_NAME
        data["timestamp"] = datetime.now(timezone.utc).isoformat()
        self.bus.publish(f"reachy/events/{self.AGENT_NAME}", data)

    def handle_context(self, payload: dict):
        """Handle demo context hints from the core loop."""
        hints = payload.get("hints", {})
        self._current_demo_context = hints.get(self.AGENT_NAME)
        if hasattr(self, "set_demo_hints"):
            self.set_demo_hints(self._current_demo_context)
        if self._current_demo_context:
            print(f"[{self.AGENT_NAME}] Demo hint: {self._current_demo_context[:80]}...")

    def _route_message(self, topic: str, payload: dict):
        """Route incoming messages to the appropriate handler."""
        if topic == "reachy/commands/goto":
            self._interaction_count += 1
            source = payload.get("source", "")
            source_tag = f" (from {source})" if source and source != "child-camera" else ""
            print(
                f"[{self.AGENT_NAME}] GOTO #{payload.get('interaction_id', '?')}: "
                f"{payload.get('object', 'unknown')}{source_tag}"
            )
            self.handle_goto(payload)
        elif topic == "reachy/commands/context":
            self.handle_context(payload)
        elif topic == "reachy/commands/system":
            self.handle_system(payload)

    def run(self):
        """Connect and start processing messages."""
        print(f"[{self.AGENT_NAME}] Starting...")

        connected = self.bus.connect()
        if connected:
            print(f"[{self.AGENT_NAME}] Connected to MQTT broker")
        elif self.args.http_port:
            self.bus.start_http_server(self.args.http_port)
            print(f"[{self.AGENT_NAME}] Running with HTTP fallback on port {self.args.http_port}")
        else:
            print(f"[{self.AGENT_NAME}] WARNING: No MQTT broker and no --http-port. No messages will be received.")

        self.bus.subscribe("reachy/commands/goto", self._route_message)
        self.bus.subscribe("reachy/commands/context", self._route_message)
        self.bus.subscribe("reachy/commands/system", self._route_message)
        self.on_start()

        print(f"[{self.AGENT_NAME}] Ready — waiting for commands...")

        try:
            self.bus.loop_forever()
        except KeyboardInterrupt:
            print(f"\n[{self.AGENT_NAME}] Shutting down.")
        finally:
            self.bus.disconnect()

    def on_start(self):
        """Override for any setup after connection is established."""
        pass
