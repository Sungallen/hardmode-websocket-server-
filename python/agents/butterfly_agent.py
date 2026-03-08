from __future__ import annotations

"""
Butterfly Agent — Claude-powered flying robot that flies toward objects.

Receives GOTO commands, asks Claude which flight behavior to use,
then sends commands to a flight controller via HTTP POST.
Runs in simulation mode when no FLIGHT_CONTROLLER_URL is set.
"""

import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.base_agent import BaseAgent
from utils.claude_agent_mixin import ClaudeAgentMixin
from utils.behavior_registry import BUTTERFLY_BEHAVIORS

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


class ButterflyAgent(BaseAgent, ClaudeAgentMixin):
    AGENT_NAME = "butterfly-agent"
    BEHAVIOR_VOCABULARY = BUTTERFLY_BEHAVIORS

    ROBOT_SYSTEM_PROMPT = """\
You are Butterfly, a small flying robot that flutters through the air.
When a child looks at something interesting, you fly toward it like a curious butterfly.

PERSONALITY: Spontaneous, playful, quick. You dart around with joyful energy.
You're fascinated by colors and movement.

You do NOT speak or narrate — you only move physically.
Choose the flight behavior that best matches the situation.\
"""

    def __init__(self):
        super().__init__()
        self.flight_url = os.environ.get("FLIGHT_CONTROLLER_URL")
        self._processing = threading.Lock()

    def add_args(self, parser):
        parser.add_argument(
            "--flight-controller", type=str, default=None,
            help="Flight controller HTTP URL (overrides FLIGHT_CONTROLLER_URL env var)"
        )

    def on_start(self):
        # CLI arg overrides env var
        if self.args.flight_controller:
            self.flight_url = self.args.flight_controller

        if self.flight_url:
            print(f"[{self.AGENT_NAME}] Flight controller: {self.flight_url}")
        else:
            print(f"[{self.AGENT_NAME}] No flight controller — simulation mode")

        if not self.args.no_claude:
            self.init_claude(model=self.args.model)
        else:
            print(f"[{self.AGENT_NAME}] Claude disabled — deterministic only")

    def handle_goto(self, payload: dict):
        if not self._processing.acquire(blocking=False):
            print(f"[{self.AGENT_NAME}] Still processing previous command, skipping")
            return

        threading.Thread(
            target=self._process_goto,
            args=(payload,),
            daemon=True,
        ).start()

    def _process_goto(self, payload: dict):
        try:
            self._do_goto(payload)
        finally:
            self._processing.release()

    def _do_goto(self, payload: dict):
        self.update_session_context(payload)
        location = payload.get("location_hint", "center")
        obj_name = payload.get("object", "unknown")

        # Try Claude
        if self._claude is not None:
            user_text = (
                f"A child is looking at: {obj_name} "
                f"({payload.get('description', '')}). "
                f"It appears to be {location} in their view. "
                f"Decide how to fly toward it or react to it."
            )
            result = self.call_claude(
                frame_b64=payload.get("frame_b64"),
                user_text=user_text,
                timeout=5.0,
            )
            if result:
                behavior = result.get("behavior", "fly_toward")
                direction = result.get("direction", location)
                print(
                    f"[{self.AGENT_NAME}] Claude chose: {behavior} "
                    f"(direction={direction}, thought={result.get('internal_thought', '')})"
                )
                self._execute_flight(behavior, direction, obj_name, payload)
                return

        # Deterministic fallback
        self._execute_flight("fly_toward", location, obj_name, payload)

    def _execute_flight(self, behavior: str, direction: str, obj_name: str, payload: dict):
        behavior_def = BUTTERFLY_BEHAVIORS.get(behavior)
        if not behavior_def:
            print(f"[{self.AGENT_NAME}] Unknown behavior '{behavior}', falling back to fly_toward")
            behavior_def = BUTTERFLY_BEHAVIORS["fly_toward"]
            behavior = "fly_toward"

        # Build flight command
        motor_map = behavior_def.get("motor_map", {})
        if isinstance(motor_map, dict) and "left" not in motor_map and direction in motor_map:
            # Direction-keyed motor map
            flight_cmd = motor_map[direction]
        else:
            flight_cmd = motor_map

        flight_cmd = dict(flight_cmd) if flight_cmd else {}
        flight_cmd["behavior"] = behavior
        flight_cmd["direction"] = direction
        flight_cmd["object"] = obj_name

        if self.flight_url and REQUESTS_AVAILABLE:
            try:
                r = requests.post(
                    f"{self.flight_url}/command",
                    json=flight_cmd,
                    timeout=5,
                )
                print(
                    f"[{self.AGENT_NAME}] Flying: {behavior} toward '{obj_name}' "
                    f"({direction}) [{r.status_code}]"
                )
            except Exception as e:
                print(f"[{self.AGENT_NAME}] Flight command failed: {e}")
        else:
            print(
                f"[{self.AGENT_NAME}] [SIM] Would fly: {behavior} "
                f"toward '{obj_name}' ({direction})"
            )

        self.publish_event({
            "event": "flight_complete",
            "interaction_id": payload.get("interaction_id"),
            "behavior": behavior,
            "direction": direction,
            "object": obj_name,
            "simulated": self.flight_url is None,
        })


if __name__ == "__main__":
    ButterflyAgent().run()
