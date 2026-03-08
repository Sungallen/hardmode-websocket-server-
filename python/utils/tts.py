from __future__ import annotations

"""Shared text-to-speech helper using gTTS + system audio player."""

import os
import subprocess
import sys
import tempfile

try:
    from gtts import gTTS
    TTS_AVAILABLE = True
except ImportError:
    TTS_AVAILABLE = False


def speak(text: str, lang: str = "en") -> None:
    """Speak text aloud via gTTS. No-op if gTTS is not installed."""
    if not TTS_AVAILABLE:
        return
    tmp_path = None
    try:
        tts = gTTS(text=text, lang=lang)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tmp_path = f.name
            tts.save(f.name)
        if sys.platform == "darwin":
            subprocess.run(["afplay", tmp_path], check=True)
        else:
            subprocess.run(["mpv", "--no-video", tmp_path], check=True)
    except Exception as e:
        agent_name = "tts"
        print(f"[{agent_name}] TTS error: {e}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
