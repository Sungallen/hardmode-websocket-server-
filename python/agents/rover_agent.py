from __future__ import annotations

"""
Rover Agent — Claude-powered wheeled robot that drives toward objects.

Receives GOTO commands, asks Claude which driving behavior to use,
then executes via differential drive motor commands (HTTP POST).
Falls back to deterministic steering map when Claude is unavailable.
"""

import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.base_agent import BaseAgent
from utils.claude_agent_mixin import ClaudeAgentMixin
from utils.behavior_registry import ROVER_BEHAVIORS
from utils.tts import speak as speak_tts

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# Deterministic fallback: (left_speed, right_speed, duration_ms) per direction
STEERING_MAP = {
    "left":   (0.3, 0.7, 800),
    "right":  (0.7, 0.3, 800),
    "center": (0.5, 0.5, 1000),
    "up":     (0.5, 0.5, 600),
    "down":   (0.4, 0.4, 500),
}


class RoverAgent(BaseAgent, ClaudeAgentMixin):
    AGENT_NAME = "rover-agent"
    BEHAVIOR_VOCABULARY = ROVER_BEHAVIORS

    ROBOT_SYSTEM_PROMPT = """\
You are Rover, a small wheeled robot that drives around exploring the world.
A child wearing camera glasses looks at interesting things, and you drive toward them.

PERSONALITY: Methodical but enthusiastic. You're a determined little explorer.
You take careful paths but get genuinely excited when you find something.

SPEAKING: When the GOTO source is another agent's discovery (like "butterfly-agent"),
you speak a SHORT excited phrase (1-2 sentences max) AND drive toward it.
Examples: "Hey! Butterfly found more sunflowers! Let's go check them out!",
"Ooh, let's go this way!", "Follow me, there's something cool over here!"

When the source is "child-camera" (the child looked at something), you do NOT speak —
you only move physically. Use "speak_and_drive" only for discoveries from other agents.\
"""

    def __init__(self):
        super().__init__()
        self.motor_url = os.environ.get("MOTOR_URL")
        self._processing = threading.Lock()

    def on_start(self):
        if self.motor_url:
            print(f"[{self.AGENT_NAME}] Motor controller: {self.motor_url}")
        else:
            print(f"[{self.AGENT_NAME}] No MOTOR_URL set — simulation mode")

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
        except Exception as e:
            print(f"[{self.AGENT_NAME}] GOTO processing failed: {e}")
        finally:
            self._processing.release()

    def build_system_prompt(self) -> str:
        base = super().build_system_prompt()
        base += (
            '\n\nWhen choosing "speak_and_drive", you MUST also include '
            '"narration": "<short excited phrase>" in your JSON response.'
        )
        return base

    def _do_goto(self, payload: dict):
        self.update_session_context(payload)
        location = payload.get("location_hint", "center")
        obj_name = payload.get("object", "unknown")
        source = payload.get("source", "child-camera")

        # Try Claude
        if self._claude is not None:
            if source != "child-camera":
                user_text = (
                    f"DISCOVERY from {source}: {obj_name} "
                    f"({payload.get('description', '')}). "
                    f"It's to the {location}. "
                    f"Say something short and excited to the child. "
                    f"Use speak_and_drive behavior."
                )
            else:
                user_text = (
                    f"A child is looking at: {obj_name} "
                    f"({payload.get('description', '')}). "
                    f"It appears to be {location} in their view. "
                    f"Decide how to drive toward it."
                )
            result = self.call_claude(
                frame_b64=payload.get("frame_b64") or None,
                user_text=user_text,
                timeout=5.0,
            )
            if result:
                behavior = result.get("behavior", "drive_toward")
                direction = result.get("direction", location)
                narration = result.get("narration")
                print(
                    f"[{self.AGENT_NAME}] Claude chose: {behavior} "
                    f"(direction={direction}, thought={result.get('internal_thought', '')})"
                )
                if narration:
                    self._speak(narration)
                self._execute_behavior(behavior, direction, obj_name, payload)
                return

        # Deterministic fallback
        if source != "child-camera":
            agent_label = source.replace("-agent", "").title()
            self._speak(
                f"Hey! {agent_label} found a {obj_name}! Let's go check it out!"
            )
        self._deterministic_drive(location, obj_name, payload)

    def _execute_behavior(self, behavior: str, direction: str, obj_name: str, payload: dict):
        behavior_def = ROVER_BEHAVIORS.get(behavior)
        if not behavior_def:
            print(f"[{self.AGENT_NAME}] Unknown behavior '{behavior}', falling back to drive_toward")
            behavior_def = ROVER_BEHAVIORS["drive_toward"]
            behavior = "drive_toward"

        if "motor_sequence" in behavior_def:
            for cmd in behavior_def["motor_sequence"]:
                self._send_motor_command(cmd, obj_name)
                time.sleep(cmd["duration_ms"] / 1000.0)
        elif "motor_map" in behavior_def:
            motor_map = behavior_def["motor_map"]
            if not motor_map:
                print(f"[{self.AGENT_NAME}] Behavior '{behavior}' has empty motor_map, skipping")
            elif isinstance(next(iter(motor_map.values())), dict):
                # Direction-keyed motor map (values are dicts like {"left": 0.3, ...})
                cmd = motor_map.get(direction, motor_map.get("center", {"left": 0.5, "right": 0.5, "duration_ms": 800}))
                self._send_motor_command(cmd, obj_name)
            else:
                # Direct motor command (values are numbers)
                self._send_motor_command(motor_map, obj_name)

        self.publish_event({
            "event": "navigation_complete",
            "interaction_id": payload.get("interaction_id"),
            "behavior": behavior,
            "direction": direction,
            "object": obj_name,
            "simulated": self.motor_url is None,
        })

    def _deterministic_drive(self, location: str, obj_name: str, payload: dict):
        left, right, duration = STEERING_MAP.get(location, (0.5, 0.5, 800))
        cmd = {"left": left, "right": right, "duration_ms": duration}
        self._send_motor_command(cmd, obj_name)

        self.publish_event({
            "event": "navigation_complete",
            "interaction_id": payload.get("interaction_id"),
            "behavior": "drive_toward",
            "direction": location,
            "object": obj_name,
            "simulated": self.motor_url is None,
        })

    def _send_motor_command(self, cmd: dict, obj_name: str):
        if self.motor_url and REQUESTS_AVAILABLE:
            try:
                r = requests.post(
                    f"{self.motor_url}/drive",
                    json=cmd,
                    timeout=5,
                )
                print(
                    f"[{self.AGENT_NAME}] Drive toward '{obj_name}' — "
                    f"L:{cmd.get('left')} R:{cmd.get('right')} "
                    f"{cmd.get('duration_ms')}ms [{r.status_code}]"
                )
            except Exception as e:
                print(f"[{self.AGENT_NAME}] Motor command failed: {e}")
        else:
            print(
                f"[{self.AGENT_NAME}] [SIM] Drive toward '{obj_name}' — "
                f"L:{cmd.get('left')} R:{cmd.get('right')} "
                f"{cmd.get('duration_ms')}ms"
            )


    def _speak(self, text: str):
        """Print and speak a short phrase (no-op if gTTS unavailable)."""
        print(f"[{self.AGENT_NAME}] Says: \"{text}\"")
        speak_tts(text, lang="en")


if __name__ == "__main__":
    RoverAgent().run()
