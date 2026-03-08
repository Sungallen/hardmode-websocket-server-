from __future__ import annotations

"""
Core Perception Loop — grabs frames from Mentra bridge, webcam, or static
image; detects sustained focus via perceptual hashing; identifies scenes via
Claude Vision or demo script metadata; and publishes GOTO and context commands
over MQTT. Supports choreographed demo mode with timed scenes and discoveries.
"""

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Load .env file if present (key=value, one per line)
_this_dir = Path(__file__).resolve().parent
_env_file = _this_dir / ".env"
if not _env_file.exists():
    _env_file = _this_dir.parent / ".env"  # oslo repo root
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))

from utils.interest_detector import InterestDetector
from utils.command_bus import CommandBus

# ─── Optional imports ────────────────────────────────────────────────────────

try:
    from anthropic import Anthropic
    CLAUDE_AVAILABLE = True
except ImportError:
    CLAUDE_AVAILABLE = False

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# ─── Configuration ───────────────────────────────────────────────────────────

CLAUDE_MODEL = "claude-sonnet-4-20250514"

SCENE_ANALYSIS_PROMPT = """\
You are analyzing a scene captured from a child's point of view through camera glasses.
Identify the main object or thing the child appears to be focused on (usually center of frame).

Respond ONLY with valid JSON in this exact format:
{
  "object": "<short name of the object>",
  "description": "<one sentence describing what you see>",
  "location_hint": "<left|right|center|above|below>",
  "category": "<toy|nature|animal|food|book|art|building|person|other>",
  "suggested_actions": ["<action 1>", "<action 2>"]
}
"""

# ─── Image sources ───────────────────────────────────────────────────────────


def capture_from_webcam(webcam_index: int = 0) -> bytes | None:
    """Capture a single frame from the webcam as JPEG bytes."""
    if not CV2_AVAILABLE:
        print("  opencv-python not found: pip install opencv-python")
        return None
    cap = cv2.VideoCapture(webcam_index)
    if not cap.isOpened():
        print("  Could not open webcam.")
        return None
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    # Discard initial frames (often dark/blurry)
    for _ in range(8):
        cap.read()
    ret, frame = cap.read()
    cap.release()
    if not ret or frame is None:
        return None
    _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return buffer.tobytes()


def fetch_from_bridge(bridge_url: str) -> bytes | None:
    """Download the latest photo from the Mentra bridge."""
    if not REQUESTS_AVAILABLE:
        print("  requests not installed: pip install requests")
        return None
    try:
        r = requests.get(f"{bridge_url}/photo", timeout=10)
        if r.status_code == 200:
            return r.content
        elif r.status_code == 503:
            print("  No photo available yet...")
    except Exception as e:
        print(f"  Bridge error: {e}")
    return None


def wait_for_bridge(bridge_url: str, timeout: int = 30) -> bool:
    """Wait for the Mentra bridge to become available."""
    if not REQUESTS_AVAILABLE:
        return False
    print(f"Waiting for Mentra bridge at {bridge_url} ...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{bridge_url}/status", timeout=2)
            if r.status_code == 200:
                print("Bridge ready.")
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


# ─── Claude Vision ───────────────────────────────────────────────────────────


def analyze_scene(claude, frame_bytes: bytes) -> dict | None:
    """Send frame to Claude Vision, return structured scene analysis."""
    image_b64 = base64.standard_b64encode(frame_bytes).decode("utf-8")

    try:
        response = claude.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=300,
            system=SCENE_ANALYSIS_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": "What is the child focused on?",
                        },
                    ],
                }
            ],
        )

        text = response.content[0].text.strip()
        # Handle markdown-wrapped JSON
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(text)

    except json.JSONDecodeError as e:
        print(f"  Claude returned invalid JSON: {e}")
        return None
    except Exception as e:
        print(f"  Claude API error: {e}")
        return None


# ─── Interest Memory ────────────────────────────────────────────────────────


class InterestMemory:
    """Sliding window memory of recently-triggered objects.
    Replaces the fixed cooldown timer with name-based deduplication."""

    def __init__(self, window_seconds: float = 60.0):
        self._recent: list[tuple[str, str, float]] = []  # (object, source, timestamp)
        self._window = window_seconds

    def has_seen(self, object_name: str, source: str = "child-camera") -> bool:
        self._prune()
        return any(
            obj.lower() == object_name.lower() and src == source
            for obj, src, _ in self._recent
        )

    def record(self, object_name: str, source: str = "child-camera"):
        self._prune()
        self._recent.append((object_name, source, time.time()))

    def recent_objects(self) -> list[str]:
        self._prune()
        seen_lower: set[str] = set()
        seen: list[str] = []
        for obj, _, _ in self._recent:
            key = obj.lower()
            if key not in seen_lower:
                seen_lower.add(key)
                seen.append(obj)
        return seen

    def _prune(self):
        cutoff = time.time() - self._window
        self._recent = [(o, s, t) for o, s, t in self._recent if t > cutoff]


# ─── Main ────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Reachy Mini — Core Perception Loop"
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Use local webcam instead of Mentra bridge"
    )
    parser.add_argument(
        "--image", type=str, default=None,
        help="Use a static image file (repeats it as frames, for testing)"
    )
    parser.add_argument(
        "--bridge", type=str, default="http://localhost:7001",
        help="Mentra bridge URL (default: http://localhost:7001)"
    )
    parser.add_argument(
        "--broker", type=str, default=None,
        help="MQTT broker hostname"
    )
    parser.add_argument(
        "--mqtt-port", type=int, default=1883,
        help="MQTT broker port (default: 1883)"
    )
    parser.add_argument(
        "--interest-time", type=float, default=3.0,
        help="Seconds of focus to trigger interest (default: 3.0)"
    )
    parser.add_argument(
        "--similarity", type=float, default=0.85,
        help="Frame similarity threshold (default: 0.85)"
    )
    parser.add_argument(
        "--http-agents", nargs="*", default=[],
        help="HTTP fallback agent endpoints (e.g. http://localhost:8001)"
    )
    parser.add_argument(
        "--frame-interval", type=float, default=0.5,
        help="Seconds between frame captures (default: 0.5 = 2 FPS)"
    )
    parser.add_argument(
        "--demo-script", type=str, default=None,
        help="Path to demo script JSON (choreographed demo mode)"
    )
    parser.add_argument(
        "--memory-window", type=float, default=60.0,
        help="Seconds to remember seen objects (default: 60)"
    )
    parser.add_argument(
        "--disk", type=str, default=None,
        help="Read frames from disk path (e.g. ../recordings/user@email.com/latest.jpg)"
    )
    args = parser.parse_args()

    # ── Load demo script (if provided) ───────────────────────────────────────

    demo_mgr = None
    if args.demo_script:
        from utils.demo_script import DemoScriptManager
        try:
            demo_mgr = DemoScriptManager(args.demo_script)
        except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
            print(f"Failed to load demo script '{args.demo_script}': {e}")
            sys.exit(1)

    # ── Check prerequisites ──────────────────────────────────────────────────

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    claude = None

    if api_key and CLAUDE_AVAILABLE:
        claude = Anthropic(api_key=api_key)
    elif demo_mgr:
        print("No API key — using demo script metadata only (no Claude Vision)")
    else:
        if not api_key:
            print("ANTHROPIC_API_KEY not set.")
            print("  export ANTHROPIC_API_KEY='sk-ant-...'")
            sys.exit(1)
        if not CLAUDE_AVAILABLE:
            print("anthropic not installed: pip install anthropic")
            sys.exit(1)

    # ── Setup image source ───────────────────────────────────────────────────

    if args.image:
        image_path = Path(args.image)
        if not image_path.exists():
            print(f"Image file not found: {args.image}")
            sys.exit(1)
        image_bytes = image_path.read_bytes()
        print(f"Static image mode — using {args.image} ({len(image_bytes):,} bytes)")
        get_photo = lambda: image_bytes
    elif args.disk:
        disk_path = Path(args.disk)
        print(f"Disk mode — reading frames from {disk_path}")
        def get_photo():
            try:
                if disk_path.exists():
                    data = disk_path.read_bytes()
                    return data if len(data) > 100 else None
                return None
            except Exception as e:
                print(f"  Disk read error: {e}")
                return None
    elif args.demo:
        if not CV2_AVAILABLE:
            print("Demo mode requires opencv-python: pip install opencv-python")
            sys.exit(1)
        print("Demo mode — using local webcam")
        get_photo = lambda: capture_from_webcam()
    else:
        if not wait_for_bridge(args.bridge):
            print("Bridge not available. Is glasses_server.ts running?")
            sys.exit(1)
        get_photo = lambda: fetch_from_bridge(args.bridge)

    # ── Setup command bus ────────────────────────────────────────────────────

    bus = CommandBus(
        mqtt_broker=args.broker,
        mqtt_port=args.mqtt_port,
        http_endpoints=args.http_agents,
    )
    mqtt_ok = bus.connect()
    if mqtt_ok:
        print(f"Connected to MQTT broker at {args.broker}")
    elif args.http_agents:
        print(f"Using HTTP fallback to: {', '.join(args.http_agents)}")
    else:
        print("WARNING: No MQTT broker and no --http-agents. Commands won't reach agents.")

    # ── Setup interest detector ──────────────────────────────────────────────

    detector = InterestDetector(
        similarity_threshold=args.similarity,
        interest_time=args.interest_time,
    )

    # ── Banner ───────────────────────────────────────────────────────────────

    if args.image:
        source_label = "Static image"
    elif args.disk:
        source_label = f"Disk ({args.disk})"
    elif args.demo:
        source_label = "Webcam (demo)"
    else:
        source_label = f"Mentra bridge ({args.bridge})"
    print("\n" + "=" * 60)
    print("  REACHY MINI — CORE PERCEPTION LOOP")
    print("=" * 60)
    print(f"  Source:      {source_label}")
    print(f"  Model:       {CLAUDE_MODEL}")
    print(f"  Focus time:  {args.interest_time}s")
    print(f"  Similarity:  {args.similarity}")
    print(f"  Memory:      {args.memory_window}s window")
    print(f"  Transport:   {'MQTT' if mqtt_ok else 'HTTP fallback'}")
    print(f"  Frame rate:  {1/args.frame_interval:.1f} FPS")
    if demo_mgr:
        print(f"  Demo script: {args.demo_script}")
    print("=" * 60 + "\n")
    print("Watching... (Ctrl+C to stop)\n")

    # ── Main loop ────────────────────────────────────────────────────────────

    interaction_id = 0
    memory = InterestMemory(args.memory_window)

    if demo_mgr:
        demo_mgr.start()

    try:
        while True:
            now = time.time()

            # ── Check demo completion ─────────────────────────────────────
            if demo_mgr and demo_mgr.is_finished():
                print("\n[DEMO] Script complete.")
                break

            if demo_mgr and demo_mgr.did_loop:
                demo_mgr.did_loop = False
                memory = InterestMemory(args.memory_window)
                detector.reset()
                print("  [DEMO] Memory cleared for loop restart")

            # ── PHASE 1: Scene changes → publish context hints ────────────
            if demo_mgr:
                changed, scene = demo_mgr.check_scene_change()
                if changed and scene:
                    detector.reset()  # rearm interest detection for new scene
                    hints = demo_mgr.get_agent_hints(scene)
                    if hints:
                        bus.publish("reachy/commands/context", {
                            "type": "context",
                            "scene_id": scene["id"],
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "hints": hints,
                        })
                    print(
                        f"  [DEMO] Scene → '{scene['id']}' "
                        f"({scene['metadata'].get('object', '?')}) — context hints published"
                    )

            # ── PHASE 2: Scheduled discoveries → source-tagged GOTOs ─────
            if demo_mgr:
                for disc in demo_mgr.get_pending_discoveries():
                    source = disc.get("source_agent", "butterfly-agent")
                    obj = disc.get("object", "unknown")
                    if memory.has_seen(obj, source):
                        continue
                    interaction_id += 1
                    print(
                        f"\n[{datetime.now().strftime('%H:%M:%S')}] "
                        f"DISCOVERY #{interaction_id} from {source}: {obj}"
                    )
                    bus.publish("reachy/commands/goto", {
                        "type": "goto",
                        "interaction_id": interaction_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "object": obj,
                        "description": disc.get("description", ""),
                        "location_hint": disc.get("location_hint", "center"),
                        "category": disc.get("category", "discovery"),
                        "suggested_actions": disc.get("suggested_actions", []),
                        "focus_duration": 0.0,
                        "source": source,
                        "frame_b64": "",
                        "recent_objects": memory.recent_objects(),
                    })
                    memory.record(obj, source)
                    print("  => Discovery GOTO published")

            # ── PHASE 3: Normal camera interest detection ─────────────────
            frame_bytes = get_photo()
            if frame_bytes is None:
                time.sleep(args.frame_interval)
                continue

            result = detector.update(frame_bytes, now)

            if result["focused"]:
                focus_duration = result["duration"]

                print(
                    f"\n[{datetime.now().strftime('%H:%M:%S')}] "
                    f"INTEREST detected "
                    f"(focused {focus_duration:.1f}s, similarity {result['similarity']:.2f})"
                )

                # Identify what we're looking at
                if demo_mgr:
                    active = demo_mgr.get_active_scene()
                    if active:
                        scene_data = demo_mgr.get_metadata_for_trigger(active)
                        print(f"  [DEMO] Using scripted metadata: {scene_data.get('object', '?')}")
                    elif claude:
                        print("  Analyzing scene with Claude...")
                        scene_data = analyze_scene(claude, frame_bytes)
                    else:
                        scene_data = None
                else:
                    if claude:
                        print("  Analyzing scene with Claude...")
                        scene_data = analyze_scene(claude, frame_bytes)
                    else:
                        scene_data = None

                if scene_data:
                    obj_name = scene_data.get("object", "unknown")

                    if memory.has_seen(obj_name, "child-camera"):
                        print(f"  => Already seen '{obj_name}' recently, skipping")
                    else:
                        interaction_id += 1
                        frame_b64 = base64.standard_b64encode(frame_bytes).decode("utf-8")
                        goto_cmd = {
                            "type": "goto",
                            "interaction_id": interaction_id,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "object": obj_name,
                            "description": scene_data.get("description", ""),
                            "location_hint": scene_data.get("location_hint", "center"),
                            "category": scene_data.get("category", "other"),
                            "suggested_actions": scene_data.get("suggested_actions", []),
                            "focus_duration": round(focus_duration, 1),
                            "source": "child-camera",
                            "frame_b64": frame_b64,
                            "recent_objects": memory.recent_objects(),
                        }

                        print(
                            f"  => {obj_name} "
                            f"({scene_data.get('category', '?')}, {scene_data.get('location_hint', '?')})"
                        )
                        print(f"  => {scene_data.get('description', '')}")

                        bus.publish("reachy/commands/goto", goto_cmd)
                        memory.record(obj_name, "child-camera")
                        print(f"  => GOTO #{interaction_id} published")
                else:
                    print("  => Scene analysis failed, skipping")

            time.sleep(args.frame_interval)

    except KeyboardInterrupt:
        print("\n\nShutting down core loop.")
        bus.publish("reachy/commands/system", {"type": "shutdown"})
    finally:
        bus.disconnect()


if __name__ == "__main__":
    main()
