from __future__ import annotations

"""
Stateful frame comparator for detecting sustained visual focus.

Uses perceptual hashing (grayscale resize to NxN, flatten to vector)
with normalized cross-correlation for similarity. The reference hash
is EMA-blended (80% old + 20% new) so gradual head movement doesn't
break tracking — only a genuine look-away resets it.

Falls back to MD5-based binary comparison if OpenCV is unavailable.
"""

import hashlib
import io
import time

import numpy as np

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


class InterestDetector:
    def __init__(
        self,
        similarity_threshold: float = 0.85,
        interest_time: float = 3.0,
        hash_size: int = 16,
        ema_alpha: float = 0.2,
    ):
        self.similarity_threshold = similarity_threshold
        self.interest_time = interest_time
        self.hash_size = hash_size
        self.ema_alpha = ema_alpha

        self._ref_hash = None       # EMA-blended reference (numpy float vector)
        self._focus_start = None     # timestamp when sustained focus began
        self._last_md5 = None        # fallback: MD5 of raw bytes
        self._triggered = False      # True after focus event consumed; rearms on look-away

    def _compute_hash(self, frame_bytes: bytes) -> np.ndarray | None:
        """Convert JPEG bytes to a flat grayscale float vector."""
        if CV2_AVAILABLE:
            arr = np.frombuffer(frame_bytes, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
            if img is None:
                return None
            resized = cv2.resize(img, (self.hash_size, self.hash_size))
            return resized.astype(np.float32).flatten()

        if PIL_AVAILABLE:
            img = Image.open(io.BytesIO(frame_bytes)).convert("L")
            img = img.resize((self.hash_size, self.hash_size))
            return np.array(img, dtype=np.float32).flatten()

        return None

    @staticmethod
    def _similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Normalized cross-correlation (dot product / norms)."""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    def _fallback_same(self, frame_bytes: bytes) -> bool:
        """MD5-based binary same/different check."""
        md5 = hashlib.md5(frame_bytes[::100]).hexdigest()  # sample every 100th byte
        if self._last_md5 is None:
            self._last_md5 = md5
            return False
        same = md5 == self._last_md5
        self._last_md5 = md5
        return same

    def update(self, frame_bytes: bytes, timestamp: float | None = None) -> dict:
        """
        Process a new frame. Returns:
            {
                "focused": bool,     # True if sustained focus threshold met
                "duration": float,   # seconds of current focus streak
                "similarity": float, # 0.0-1.0, current frame vs reference
            }
        """
        if timestamp is None:
            timestamp = time.time()

        current_hash = self._compute_hash(frame_bytes)

        # Fallback: no image library available
        if current_hash is None:
            same = self._fallback_same(frame_bytes)
            if same:
                if self._focus_start is None:
                    self._focus_start = timestamp
                duration = timestamp - self._focus_start
                is_focused = duration >= self.interest_time and not self._triggered
                if is_focused:
                    self._triggered = True
                return {
                    "focused": is_focused,
                    "duration": duration,
                    "similarity": 1.0,
                }
            else:
                self._focus_start = None
                self._triggered = False
                return {"focused": False, "duration": 0.0, "similarity": 0.0}

        # First frame: initialize reference
        if self._ref_hash is None:
            self._ref_hash = current_hash
            self._focus_start = timestamp
            return {"focused": False, "duration": 0.0, "similarity": 1.0}

        sim = self._similarity(self._ref_hash, current_hash)

        if sim >= self.similarity_threshold:
            # Still looking at the same thing — blend into reference
            self._ref_hash = (
                (1 - self.ema_alpha) * self._ref_hash
                + self.ema_alpha * current_hash
            )
            if self._focus_start is None:
                self._focus_start = timestamp
            duration = timestamp - self._focus_start
            is_focused = duration >= self.interest_time and not self._triggered
            if is_focused:
                self._triggered = True  # consumed — rearms on look-away
            return {
                "focused": is_focused,
                "duration": duration,
                "similarity": sim,
            }
        else:
            # Looked away — reset reference and rearm trigger
            self._ref_hash = current_hash
            self._focus_start = timestamp
            self._triggered = False
            return {"focused": False, "duration": 0.0, "similarity": sim}

    def reset(self):
        """Clear all state. Use on scene changes to rearm detection."""
        self._ref_hash = None
        self._focus_start = None
        self._last_md5 = None
        self._triggered = False
