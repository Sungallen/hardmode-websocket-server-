from __future__ import annotations

"""
Claude conversation mixin for sub-agents.

Provides persistent Claude conversation management with:
- Sliding window history (strips old images to manage token cost)
- System prompt composition from robot identity + behavior vocabulary + session context
- Structured JSON output parsing
- Graceful fallback when Claude is unavailable
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

# Load .env file if present
_project_root = Path(__file__).resolve().parent.parent
_env_file = _project_root / ".env"
if not _env_file.exists():
    _env_file = _project_root.parent / ".env"  # oslo repo root
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))

try:
    from anthropic import Anthropic
    _CLAUDE_AVAILABLE = True
except ImportError:
    _CLAUDE_AVAILABLE = False


class ClaudeAgentMixin:
    """Mixin that adds Claude conversation management to a BaseAgent.

    Subclasses must define:
        ROBOT_SYSTEM_PROMPT: str — identity and personality
        BEHAVIOR_VOCABULARY: dict — from behavior_registry
    """

    # Subclass must set these
    ROBOT_SYSTEM_PROMPT: str = ""
    BEHAVIOR_VOCABULARY: dict = {}

    def init_claude(self, model: str = "claude-sonnet-4-20250514", max_history: int = 8):
        """Initialize the Claude client and conversation state."""
        self._claude = None
        self._claude_model = model
        self._max_history = max_history
        self._conversation_history: list[dict] = []
        self._session_context = {
            "objects_seen": [],
            "interaction_count": 0,
            "session_start": datetime.now(timezone.utc).isoformat(),
        }
        self._demo_hints: str | None = None

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key or not _CLAUDE_AVAILABLE:
            agent_name = getattr(self, "AGENT_NAME", "agent")
            print(f"[{agent_name}] Claude unavailable — deterministic fallback only")
            return

        self._claude = Anthropic(api_key=api_key)
        agent_name = getattr(self, "AGENT_NAME", "agent")
        print(f"[{agent_name}] Claude ready (model: {model})")

    def set_demo_hints(self, hints: str | None):
        """Store a demo context hint. Injected into system prompt on next call."""
        self._demo_hints = hints

    def build_system_prompt(self) -> str:
        """Compose the full system prompt from identity + behaviors + session + format."""
        sections = [self.ROBOT_SYSTEM_PROMPT]

        # Available behaviors
        if self.BEHAVIOR_VOCABULARY:
            lines = ["Your available physical behaviors:"]
            for name, info in self.BEHAVIOR_VOCABULARY.items():
                req = ""
                if "requires" in info:
                    req = f" (requires: {', '.join(info['requires'])})"
                lines.append(f'- "{name}": {info["description"]}{req}')
            lines.append("\nYou MUST choose one of these behaviors. Do not invent new ones.")
            sections.append("\n".join(lines))

        # Session context
        ctx = self._session_context
        seen = ", ".join(ctx["objects_seen"][-10:]) if ctx["objects_seen"] else "nothing yet"
        sections.append(
            f"SESSION SO FAR:\n"
            f"- Interaction count: {ctx['interaction_count']}\n"
            f"- Objects seen: {seen}"
        )

        # Demo context hints (if active)
        if self._demo_hints:
            sections.append(
                f"DEMO CONTEXT (use this to inform your response):\n{self._demo_hints}"
            )

        # Output format
        sections.append(
            "Respond with ONLY valid JSON (no markdown, no explanation):\n"
            '{"behavior": "<one of your available behaviors>", '
            '"direction": "<left|right|center|up|down>", '
            '"intensity": "<gentle|normal|excited>", '
            '"internal_thought": "<one-line reasoning>"}'
        )

        return "\n\n".join(sections)

    def call_claude(self, frame_b64: str | None, user_text: str, timeout: float = 10.0) -> dict | None:
        """Send a message to Claude and parse the JSON response.

        Returns parsed dict on success, None on API error.
        On JSON parse failure, returns {"behavior": "idle", ...} with the raw text.
        """
        if self._claude is None:
            return None

        # Build message content
        content: list[dict] = []
        if frame_b64:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_b64},
            })
        content.append({"type": "text", "text": user_text})

        message = {"role": "user", "content": content}
        self._conversation_history.append(message)

        # Prepare messages: strip images from older turns to save tokens
        messages_to_send = self._conversation_history[-self._max_history:]
        messages_to_send = self._strip_old_images(messages_to_send)

        try:
            response = self._claude.messages.create(
                model=self._claude_model,
                max_tokens=300,
                system=self.build_system_prompt(),
                messages=messages_to_send,
                timeout=timeout,
            )
            reply_text = response.content[0].text.strip()
            self._conversation_history.append({"role": "assistant", "content": reply_text})

            # Trim history
            if len(self._conversation_history) > self._max_history + 2:
                self._conversation_history = self._conversation_history[-self._max_history:]

            # Parse JSON (handle markdown-wrapped responses)
            return self._parse_json(reply_text)

        except json.JSONDecodeError:
            return {"behavior": "idle", "internal_thought": "failed to parse JSON"}
        except Exception as e:
            agent_name = getattr(self, "AGENT_NAME", "agent")
            print(f"[{agent_name}] Claude error: {e}")
            self._conversation_history.pop()
            return None

    def update_session_context(self, payload: dict):
        """Update session tracking with a new GOTO payload."""
        self._session_context["interaction_count"] += 1
        obj = payload.get("object", "unknown")
        if obj not in self._session_context["objects_seen"]:
            self._session_context["objects_seen"].append(obj)

    def _parse_json(self, text: str) -> dict:
        """Parse JSON from Claude's response, handling markdown wrapping."""
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"behavior": "idle", "internal_thought": f"unparseable: {text[:100]}"}

    def _strip_old_images(self, messages: list[dict]) -> list[dict]:
        """Remove base64 images from all but the last 2 user messages."""
        if len(messages) <= 2:
            return messages

        result = []
        user_count_from_end = 0
        # Count user messages from the end
        for msg in reversed(messages):
            if msg["role"] == "user":
                user_count_from_end += 1

        seen_users = 0
        for msg in messages:
            if msg["role"] == "user":
                seen_users += 1
                if seen_users <= user_count_from_end - 2:
                    # Strip images from older user messages
                    if isinstance(msg.get("content"), list):
                        stripped = {
                            "role": "user",
                            "content": [
                                block for block in msg["content"]
                                if block.get("type") != "image"
                            ],
                        }
                        result.append(stripped)
                        continue
            result.append(msg)

        return result
