from __future__ import annotations

"""
Behavior vocabularies for each robot type.

Each behavior has:
  - description: shown to Claude so it knows what the behavior does
  - animation / motor_map / motor_sequence: used by the deterministic executor

Claude picks a behavior name; the executor handles the hardware.
"""

REACHY_BEHAVIORS = {
    "look_curious": {
        "description": "Tilt head up slightly as if examining something interesting",
        "animation": "_anim_thinking",
    },
    "look_excited": {
        "description": "Quick head wiggles showing excitement and delight",
        "animation": "_anim_excited",
    },
    "look_happy": {
        "description": "Gentle head nod showing contentment and approval",
        "animation": "_anim_happy",
    },
    "look_at_direction": {
        "description": "Turn head toward a direction to look at the object (requires direction: left/right/center/up/down)",
        "animation": "_anim_look_direction",
        "requires": ["direction"],
    },
    "lean_in": {
        "description": "Lean head forward as if getting a closer look at something fascinating",
        "animation": "_anim_lean_in",
    },
    "idle": {
        "description": "Stay still and observe quietly",
        "animation": None,
    },
}

ROVER_BEHAVIORS = {
    "drive_toward": {
        "description": "Drive toward the object in the given direction (requires direction: left/right/center/up/down)",
        "requires": ["direction"],
        "motor_map": {
            "left":   {"left": 0.3, "right": 0.7, "duration_ms": 800},
            "right":  {"left": 0.7, "right": 0.3, "duration_ms": 800},
            "center": {"left": 0.5, "right": 0.5, "duration_ms": 1000},
            "up":     {"left": 0.5, "right": 0.5, "duration_ms": 600},
            "down":   {"left": 0.4, "right": 0.4, "duration_ms": 500},
        },
    },
    "circle_around": {
        "description": "Drive in a small circle around the object to explore it from different angles",
        "motor_map": {"left": 0.6, "right": 0.2, "duration_ms": 2000},
    },
    "back_up": {
        "description": "Reverse away from the object slowly",
        "motor_map": {"left": -0.3, "right": -0.3, "duration_ms": 600},
    },
    "wiggle": {
        "description": "Quick left-right wiggle to show excitement about the discovery",
        "motor_sequence": [
            {"left": 0.4, "right": -0.4, "duration_ms": 200},
            {"left": -0.4, "right": 0.4, "duration_ms": 200},
            {"left": 0.0, "right": 0.0, "duration_ms": 100},
        ],
    },
    "speak_and_drive": {
        "description": "Say a short excited phrase to the child AND drive toward something. Use when another agent (like Butterfly) discovered something new. (requires direction, narration)",
        "requires": ["direction", "narration"],
        "motor_map": {
            "left":   {"left": 0.3, "right": 0.7, "duration_ms": 800},
            "right":  {"left": 0.7, "right": 0.3, "duration_ms": 800},
            "center": {"left": 0.5, "right": 0.5, "duration_ms": 1000},
            "up":     {"left": 0.5, "right": 0.5, "duration_ms": 600},
            "down":   {"left": 0.4, "right": 0.4, "duration_ms": 500},
        },
    },
    "idle": {
        "description": "Stay still and wait",
        "motor_map": {"left": 0.0, "right": 0.0, "duration_ms": 0},
    },
}

BUTTERFLY_BEHAVIORS = {
    "fly_toward": {
        "description": "Fly toward the object in the given direction (requires direction: left/right/center/up/down)",
        "requires": ["direction"],
        "motor_map": {
            "left":   {"servo": "yaw", "angle": -30, "speed": 0.5, "duration_ms": 1000},
            "right":  {"servo": "yaw", "angle": 30, "speed": 0.5, "duration_ms": 1000},
            "center": {"servo": "pitch", "angle": 0, "speed": 0.6, "duration_ms": 800},
            "up":     {"servo": "pitch", "angle": 20, "speed": 0.4, "duration_ms": 800},
            "down":   {"servo": "pitch", "angle": -15, "speed": 0.4, "duration_ms": 800},
        },
    },
    "hover": {
        "description": "Hover in place near the object, wings fluttering gently",
        "motor_map": {"servo": "wings", "pattern": "gentle", "duration_ms": 2000},
    },
    "circle_above": {
        "description": "Fly in a circle above the object, looking down at it",
        "motor_map": {"servo": "yaw", "pattern": "circle", "speed": 0.3, "duration_ms": 3000},
    },
    "land_near": {
        "description": "Descend gently and land near the object",
        "motor_map": {"servo": "pitch", "angle": -20, "speed": 0.2, "duration_ms": 1500},
    },
    "flutter_excited": {
        "description": "Flutter wings rapidly to show excitement about the discovery",
        "motor_map": {"servo": "wings", "pattern": "excited", "duration_ms": 1000},
    },
    "idle": {
        "description": "Stay perched quietly, wings folded",
        "motor_map": {"servo": "wings", "pattern": "rest", "duration_ms": 0},
    },
}
