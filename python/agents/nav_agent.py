from __future__ import annotations

"""
Navigation Agent — receives GOTO commands and steers a robotic car
toward the object using the location_hint field.

Sends motor commands via HTTP POST to a motor controller endpoint.
Runs in simulation mode if no MOTOR_URL is set.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.base_agent import BaseAgent

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


# Maps location hints to differential drive commands (left_power, right_power)
STEERING_MAP = {
    "left":   (0.3, 0.7, 800),   # turn left
    "right":  (0.7, 0.3, 800),   # turn right
    "center": (0.5, 0.5, 1000),  # drive straight
    "above":  (0.5, 0.5, 600),   # short forward (object is above eye level)
    "below":  (0.4, 0.4, 500),   # slow forward (object on ground)
}


class NavAgent(BaseAgent):
    AGENT_NAME = "nav-agent"

    def __init__(self):
        super().__init__()
        self.motor_url = os.environ.get("MOTOR_URL")
        if self.motor_url:
            print(f"[{self.AGENT_NAME}] Motor controller: {self.motor_url}")
        else:
            print(f"[{self.AGENT_NAME}] No MOTOR_URL set — simulation mode")

    def handle_goto(self, payload: dict):
        location = payload.get("location_hint", "center")
        obj_name = payload.get("object", "unknown")
        left, right, duration = STEERING_MAP.get(location, (0.5, 0.5, 800))

        motor_cmd = {
            "left": left,
            "right": right,
            "duration_ms": duration,
        }

        if self.motor_url and REQUESTS_AVAILABLE:
            # Real hardware
            try:
                r = requests.post(
                    f"{self.motor_url}/drive",
                    json=motor_cmd,
                    timeout=5,
                )
                print(
                    f"[{self.AGENT_NAME}] Driving toward '{obj_name}' "
                    f"({location}) — L:{left} R:{right} {duration}ms "
                    f"[{r.status_code}]"
                )
            except Exception as e:
                print(f"[{self.AGENT_NAME}] Motor command failed: {e}")
        else:
            # Simulation
            print(
                f"[{self.AGENT_NAME}] [SIM] Would drive toward '{obj_name}' "
                f"({location}) — L:{left} R:{right} {duration}ms"
            )

        self.publish_event({
            "event": "navigation_complete",
            "interaction_id": payload.get("interaction_id"),
            "object": obj_name,
            "location_hint": location,
            "motor_command": motor_cmd,
            "simulated": self.motor_url is None,
        })


if __name__ == "__main__":
    NavAgent().run()
