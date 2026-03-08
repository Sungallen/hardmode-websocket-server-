from __future__ import annotations

"""
Demo script manager — loads a JSON timeline of scenes with metadata,
agent hints, and scheduled discoveries. Used by core_loop.py to
choreograph multi-agent demos without relying on Claude Vision.
"""

import json
import time


class DemoScriptManager:
    def __init__(self, script_path: str):
        with open(script_path) as f:
            self._script = json.load(f)

        self._meta = self._script.get("meta", {})
        self._scenes = self._script.get("scenes", [])
        self._scenes.sort(key=lambda s: s["start_seconds"])

        self._start_time: float | None = None
        self._active_scene_id: str | None = None
        self._fired_discoveries: set[tuple[str, int]] = set()
        self.did_loop = False

        self._validate()

        title = self._meta.get("title", "untitled")
        duration = self._meta.get("total_duration_seconds", "?")
        print(f"[DemoScript] Loaded '{title}' — {len(self._scenes)} scenes, {duration}s")

    def _validate(self):
        if not self._scenes:
            raise ValueError("Demo script has no scenes")

        for scene in self._scenes:
            for field in ("id", "start_seconds", "end_seconds"):
                if field not in scene:
                    raise ValueError(f"Scene missing required field: {field}")
            if scene["end_seconds"] <= scene["start_seconds"]:
                raise ValueError(
                    f"Scene '{scene['id']}': end_seconds must be > start_seconds"
                )
            for i, disc in enumerate(scene.get("discoveries", [])):
                if "at_seconds" not in disc:
                    raise ValueError(
                        f"Scene '{scene['id']}' discovery {i}: missing at_seconds"
                    )
                if disc["at_seconds"] >= scene["end_seconds"]:
                    print(
                        f"[DemoScript] WARNING: Scene '{scene['id']}' discovery {i} "
                        f"at_seconds={disc['at_seconds']} >= end_seconds={scene['end_seconds']}"
                    )

        # Check for overlapping scenes
        for i in range(len(self._scenes) - 1):
            a, b = self._scenes[i], self._scenes[i + 1]
            if a["end_seconds"] > b["start_seconds"]:
                print(
                    f"[DemoScript] WARNING: Scenes '{a['id']}' and '{b['id']}' overlap"
                )

    def start(self):
        self._start_time = time.time()
        title = self._meta.get("title", "untitled")
        print(f"[DemoScript] Started '{title}'")

    def elapsed(self) -> float:
        if self._start_time is None:
            return 0.0
        return time.time() - self._start_time

    def is_finished(self) -> bool:
        total = self._meta.get("total_duration_seconds")
        if total is None:
            return False
        elapsed = self.elapsed()
        if elapsed >= total:
            if self._meta.get("loop", False):
                self._start_time = time.time()
                self._fired_discoveries.clear()
                self._active_scene_id = None
                self.did_loop = True
                print("[DemoScript] Looping — restarting timeline")
                return False
            return True
        return False

    def get_active_scene(self) -> dict | None:
        elapsed = self.elapsed()
        for scene in self._scenes:
            if scene["start_seconds"] <= elapsed < scene["end_seconds"]:
                return scene
        return None

    def check_scene_change(self) -> tuple[bool, dict | None]:
        scene = self.get_active_scene()
        new_id = scene["id"] if scene else None
        changed = new_id != self._active_scene_id
        if changed:
            self._active_scene_id = new_id
        return (changed, scene)

    def get_metadata_for_trigger(self, scene: dict) -> dict:
        meta = scene.get("metadata", {})
        return {
            "object": meta.get("object", "unknown"),
            "description": meta.get("description", ""),
            "location_hint": meta.get("location_hint", "center"),
            "category": meta.get("category", "other"),
            "suggested_actions": meta.get("suggested_actions", []),
        }

    def get_agent_hints(self, scene: dict) -> dict[str, str]:
        return scene.get("agent_hints", {})

    def get_pending_discoveries(self) -> list[dict]:
        scene = self.get_active_scene()
        if not scene:
            return []
        elapsed = self.elapsed()
        results = []
        for i, disc in enumerate(scene.get("discoveries", [])):
            key = (scene["id"], i)
            if key not in self._fired_discoveries and disc["at_seconds"] <= elapsed:
                self._fired_discoveries.add(key)
                results.append(disc)
        return results
