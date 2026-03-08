from __future__ import annotations

"""
Narrate Agent — receives GOTO commands, calls Claude with an image frame
or demo context in a creative mode (WONDER/STORY/CHALLENGE cycling),
and speaks the response aloud via the shared TTS helper.
"""

import os
import sys
from itertools import cycle
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

# Load .env file if present
_env_file = _project_root / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))

from utils.base_agent import BaseAgent
from utils.tts import speak as speak_tts

try:
    from anthropic import Anthropic
    CLAUDE_AVAILABLE = True
except ImportError:
    CLAUDE_AVAILABLE = False


CREATIVE_MODES = cycle(["WONDER", "STORY", "CHALLENGE"])

MODE_INSTRUCTIONS = {
    "WONDER": (
        "You are SUPER excited about what you see — let that energy burst through! "
        "In exactly 4-5 short, punchy sentences, find one magical or surprising thing in the scene "
        "and give it a secret life or hidden power. "
        "Be warm, friendly and fizzing with energy. "
        "End with ONE fun open question that sparks their imagination."
    ),
    "STORY": (
        "You are SO excited to tell this story — you can barely contain yourself! "
        "In exactly 4-5 short vivid sentences, invent a fun story starring something you see. "
        "Give it a character, a problem and a surprising twist. "
        "Keep it friendly, energetic and easy to follow. "
        "End with a cliffhanger: what do YOU think happens next?"
    ),
    "CHALLENGE": (
        "You are bursting with excitement to give the child this special mission! "
        "In exactly 4-5 short, energetic sentences, make them feel chosen and amazing. "
        "Then give ONE concrete creative challenge using something they can see right now. "
        "Keep it warm, fun and totally doable. "
        "Make them feel like a hero."
    ),
}

SYSTEM_PROMPT_EN = """\
You are Reachy, a super excited, warm and creative robot buddy for kids aged 6-9.

STYLE: You always sound genuinely thrilled and friendly — like the coolest friend ever.
You express excitement naturally: "Oh wow!", "This is SO cool!", "I love this!"
FORMAT: Exactly 4-5 short punchy sentences. No lists. No formatting.
Always end with ONE fun question or challenge.
NEVER: more than 5 sentences, fears, dangers, condescension.\
"""

SYSTEM_PROMPT_ES = """\
Eres Reachy, un robot amigo superemocionado y creativo para ninos de 6-9 anos.

ESTILO: Siempre suenas emocionado, calido y lleno de energia — como el mejor amigo del mundo.
FORMATO: Exactamente 4-5 frases cortas y directas. Sin listas. Sin formato.
Termina siempre con UNA pregunta o reto divertido.
NUNCA: mas de 5 frases, miedos, peligros, condescendencia.\
"""


class NarrateAgent(BaseAgent):
    AGENT_NAME = "identify-agent"

    def __init__(self):
        super().__init__()
        self.lang = self.args.lang
        self.claude = None
        self.conversation_history = []
        self.max_history = 8

    def add_args(self, parser):
        parser.add_argument(
            "--lang", type=str, default="en", choices=["en", "es"],
            help="Language (default: en)"
        )
        parser.add_argument(
            "--model", type=str, default="claude-sonnet-4-20250514",
            help="Claude model for narration"
        )

    def on_start(self):
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key or not CLAUDE_AVAILABLE:
            print(f"[{self.AGENT_NAME}] WARNING: No Claude API — will print descriptions only")
        else:
            self.claude = Anthropic(api_key=api_key)
            print(f"[{self.AGENT_NAME}] Claude ready (model: {self.args.model})")

        from utils.tts import TTS_AVAILABLE
        if not TTS_AVAILABLE:
            print(f"[{self.AGENT_NAME}] gTTS not available — text output only")

    def handle_goto(self, payload: dict):
        frame_b64 = payload.get("frame_b64")
        obj_name = payload.get("object", "something")
        mode = next(CREATIVE_MODES)

        print(f"[{self.AGENT_NAME}] Mode: {mode} | Object: {obj_name}")

        if self.claude and frame_b64:
            narration = self._generate_narration(frame_b64, mode, obj_name)
        elif self.claude and self._current_demo_context:
            narration = self._generate_narration(None, mode, obj_name)
        else:
            narration = f"I see {obj_name}! {payload.get('description', 'How interesting!')}"

        self._speak(narration)

        self.publish_event({
            "event": "narration_complete",
            "interaction_id": payload.get("interaction_id"),
            "mode": mode,
            "narration": narration,
            "object": obj_name,
        })

    def _generate_narration(self, frame_b64: str | None, mode: str, obj_name: str = "") -> str:
        """Call Claude with an image frame or demo context, and return narration text."""
        system_prompt = SYSTEM_PROMPT_ES if self.lang == "es" else SYSTEM_PROMPT_EN
        instruction = MODE_INSTRUCTIONS[mode]

        if frame_b64:
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": frame_b64,
                    },
                },
                {"type": "text", "text": instruction},
            ]
        else:
            enhanced = (
                f"Context from demo: {self._current_demo_context}\n"
                f"The child is looking at: {obj_name}\n\n"
                f"{instruction}"
            )
            content = [{"type": "text", "text": enhanced}]

        message = {"role": "user", "content": content}
        self.conversation_history.append(message)
        messages_to_send = self.conversation_history[-self.max_history:]

        try:
            response = self.claude.messages.create(
                model=self.args.model,
                max_tokens=300,
                system=system_prompt,
                messages=messages_to_send,
            )
            reply = response.content[0].text.strip()
            self.conversation_history.append({"role": "assistant", "content": reply})

            if len(self.conversation_history) > self.max_history + 2:
                self.conversation_history = self.conversation_history[-self.max_history:]

            return reply
        except Exception as e:
            print(f"[{self.AGENT_NAME}] Claude error: {e}")
            self.conversation_history.pop()  # remove orphaned user message
            fallback_obj = obj_name or "that"
            return f"Wow, look at {fallback_obj}! Tell me more about what you see!"

    def _speak(self, text: str):
        """Print and speak the narration (no-op if gTTS unavailable)."""
        print(f"\n{'─' * 60}")
        print(f"  Reachy says:")
        print(f"  {text}")
        print(f"{'─' * 60}\n")
        speak_tts(text, lang=self.lang)


if __name__ == "__main__":
    NarrateAgent().run()
