from __future__ import annotations

"""
Body Agent — Claude-powered Reachy Mini robot head animations.

Two-phase execution:
  Phase 1 (immediate): Play "thinking" animation so the robot feels responsive
  Phase 2 (background): Ask Claude which behavior to play, then execute it

Also subscribes to narrate agent events to play "excited" after narration.
Silently no-ops if no Reachy hardware is connected.
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.base_agent import BaseAgent
from utils.claude_agent_mixin import ClaudeAgentMixin
from utils.behavior_registry import REACHY_BEHAVIORS

try:
    from reachy_sdk import ReachySDK
    REACHY_AVAILABLE = True
except ImportError:
    REACHY_AVAILABLE = False


class BodyAgent(BaseAgent, ClaudeAgentMixin):
    AGENT_NAME = "body-agent"
    BEHAVIOR_VOCABULARY = REACHY_BEHAVIORS

    ROBOT_SYSTEM_PROMPT = """\
You are the physical body controller for Reachy, a humanoid robot companion for children aged 6-9.
You control Reachy's head movements to express emotions and reactions.

PERSONALITY: Curious, warm, expressive. You react physically to what the child sees.
When something interesting appears, you show genuine curiosity through head movements.

You do NOT speak — another agent handles narration. You only control physical movement.
Choose the head behavior that best expresses your reaction to what the child is looking at.\
"""

    def __init__(self):
        super().__init__()
        self.reachy = None
        self._processing = threading.Lock()

    def add_args(self, parser):
        parser.add_argument(
            "--reachy-ip", type=str, default="localhost",
            help="Reachy Mini IP address (default: localhost)"
        )

    def on_start(self):
        # Subscribe to narrate agent events for post-narration animation
        self.bus.subscribe("reachy/events/identify-agent", self._on_narration)

        # Initialize Reachy hardware
        if not REACHY_AVAILABLE:
            print(f"[{self.AGENT_NAME}] reachy_sdk not installed — animation disabled")
        else:
            try:
                self.reachy = ReachySDK(host=self.args.reachy_ip)
                print(f"[{self.AGENT_NAME}] Connected to Reachy at {self.args.reachy_ip}")
            except Exception as e:
                print(f"[{self.AGENT_NAME}] Could not connect to Reachy: {e}")
                self.reachy = None

        # Initialize Claude brain
        if not self.args.no_claude:
            self.init_claude(model=self.args.model)
        else:
            print(f"[{self.AGENT_NAME}] Claude disabled — deterministic only")

    def handle_goto(self, payload: dict):
        obj_name = payload.get("object", "something")

        # Phase 1: Immediate "thinking" animation (no Claude needed)
        print(f"[{self.AGENT_NAME}] Thinking about '{obj_name}'...")
        self._run_anim(self._anim_thinking)

        # Phase 2: Ask Claude for a follow-up behavior (background thread)
        if self._claude is not None:
            threading.Thread(
                target=self._claude_decide,
                args=(payload,),
                daemon=True,
            ).start()

    def _claude_decide(self, payload: dict):
        if not self._processing.acquire(blocking=False):
            return

        try:
            self.update_session_context(payload)
            obj_name = payload.get("object", "unknown")
            location = payload.get("location_hint", "center")

            user_text = (
                f"A child is looking at: {obj_name} "
                f"({payload.get('description', '')}). "
                f"It's {location} in their view. "
                f"Choose a head movement to express your reaction."
            )
            result = self.call_claude(
                frame_b64=payload.get("frame_b64"),
                user_text=user_text,
                timeout=10.0,
            )
            if result:
                behavior = result.get("behavior", "idle")
                direction = result.get("direction")
                print(
                    f"[{self.AGENT_NAME}] Claude chose: {behavior} "
                    f"(thought={result.get('internal_thought', '')})"
                )
                self._execute_behavior(behavior, direction)

                self.publish_event({
                    "event": "body_behavior_complete",
                    "interaction_id": payload.get("interaction_id"),
                    "behavior": behavior,
                    "object": obj_name,
                })
        finally:
            self._processing.release()

    def _execute_behavior(self, behavior: str, direction: str | None = None):
        behavior_def = REACHY_BEHAVIORS.get(behavior)
        if not behavior_def or not behavior_def.get("animation"):
            return

        anim_name = behavior_def["animation"]
        anim_fn = getattr(self, anim_name, None)
        if not anim_fn:
            return

        if behavior == "look_at_direction" and direction:
            self._run_anim(anim_fn, direction)
        else:
            self._run_anim(anim_fn)

    def _on_narration(self, topic: str, payload: dict):
        print(f"[{self.AGENT_NAME}] Narration done — excited!")
        self._run_anim(self._anim_excited)

    def _run_anim(self, fn, *args):
        threading.Thread(target=self._safe_move, args=(fn, *args), daemon=True).start()

    def _safe_move(self, fn, *args):
        try:
            fn(*args)
        except Exception:
            pass

    # ── Animations ──────────────────────────────────────────────────────

    def _anim_thinking(self):
        if not self.reachy:
            return
        self.reachy.head.look_at(x=1, y=0, z=0.18, duration=0.7)
        time.sleep(0.6)
        self.reachy.head.look_at(x=1, y=0, z=0, duration=0.7)

    def _anim_excited(self):
        if not self.reachy:
            return
        for _ in range(2):
            self.reachy.head.look_at(x=1, y=0.15, z=0.1, duration=0.25)
            time.sleep(0.15)
            self.reachy.head.look_at(x=1, y=-0.15, z=0.1, duration=0.25)
            time.sleep(0.15)
        self.reachy.head.look_at(x=1, y=0, z=0, duration=0.3)

    def _anim_happy(self):
        if not self.reachy:
            return
        self.reachy.head.look_at(x=1, y=0, z=0, duration=0.4)
        time.sleep(0.2)
        self.reachy.head.look_at(x=1, y=0.12, z=0.08, duration=0.35)
        time.sleep(0.2)
        self.reachy.head.look_at(x=1, y=0, z=0, duration=0.35)

    def _anim_lean_in(self):
        if not self.reachy:
            return
        self.reachy.head.look_at(x=0.8, y=0, z=-0.05, duration=0.5)
        time.sleep(0.4)
        self.reachy.head.look_at(x=1, y=0, z=0, duration=0.5)

    def _anim_look_direction(self, direction: str = "center"):
        if not self.reachy:
            return
        direction_coords = {
            "left":   (1, 0.2, 0, 0.4),
            "right":  (1, -0.2, 0, 0.4),
            "center": (1, 0, 0, 0.3),
            "up":     (1, 0, 0.15, 0.4),
            "down":   (1, 0, -0.1, 0.4),
        }
        x, y, z, dur = direction_coords.get(direction, (1, 0, 0, 0.3))
        self.reachy.head.look_at(x=x, y=y, z=z, duration=dur)
        time.sleep(dur)
        self.reachy.head.look_at(x=1, y=0, z=0, duration=0.3)


if __name__ == "__main__":
    BodyAgent().run()
