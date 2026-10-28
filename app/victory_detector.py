"""Detect the large white 斩 victory animation seen in the supplied demo.

This is a narrow visual template, not a general enemy-defeat model. It does not
read timestamps, enemy names, or health bars. The caller must associate a recent
boss name and deduplicate adjacent hits. Use <= 0.5 s sampling: the opaque glyph
is visible for less than one second. Calibrated on 16:9 SDR footage only.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


class VictoryDetector:
    """Match the actual glyph silhouette; uniform flashes do not match.

    ``frame`` is a uint8 NumPy RGB/BGR (or grayscale) image. ``confidence`` is
    normalized template correlation, not a calibrated probability. No result is
    returned below the conservative threshold, including faded-out animations.
    """

    _SIZE = (960, 540)
    _ROI = (380, 10, 820, 530)
    _SCALES = (0.8, 0.9, 1.0, 1.1, 1.2)

    def __init__(self, threshold: float = 0.65):
        if not 0.5 <= threshold <= 1.0:
            raise ValueError("victory threshold must be between 0.5 and 1")
        self.threshold = float(threshold)
        path = Path(__file__).with_name("assets") / "victory_zhan.png"
        template = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
        if template is None or template.shape != (420, 300) or template.std() < 1:
            raise ValueError("Invalid victory glyph template")
        self._templates = [
            (scale, cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST))
            for scale in self._SCALES
        ]

    @classmethod
    def _white_ink(cls, frame: np.ndarray) -> np.ndarray:
        image = cv2.resize(frame, cls._SIZE, interpolation=cv2.INTER_AREA)
        if image.ndim == 2:
            return (image > 210).astype(np.uint8) * 255
        image = image[:, :, :3]
        # Channel-symmetric test: both decoder BGR and caller RGB are supported.
        low, high = image.min(axis=2), image.max(axis=2)
        return ((low > 210) & ((high - low) < 42)).astype(np.uint8) * 255

    def _match(self, frame: np.ndarray) -> dict | None:
        if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8
                or frame.ndim not in (2, 3) or min(frame.shape[:2]) < 180
                or (frame.ndim == 3 and frame.shape[2] not in (3, 4))):
            return None
        height, width = frame.shape[:2]
        if not 1.7 <= width / height <= 1.85:
            return None  # Different aspect ratios require another calibration.
        x0, y0, x1, y1 = self._ROI
        mask = self._white_ink(frame)[y0:y1, x0:x1]
        if not mask.any() or mask.min() == 255:
            return None
        best = None
        for scale, template in self._templates:
            correlation = cv2.matchTemplate(mask, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, location = cv2.minMaxLoc(correlation)
            if best is None or score > best["confidence"]:
                x, y = location
                h, w = template.shape
                best = {
                    "confidence": max(0.0, min(float(score), 1.0)),
                    "method": "template",
                    "text": "斩",
                    "name": "胜利提示",
                    "template": "victory_zhan",
                    "scale": scale,
                    "bbox": [round((x + x0) * width / 960), round((y + y0) * height / 540),
                             round(w * width / 960), round(h * height / 540)],
                }
        return best

    def detect(self, frame: np.ndarray) -> dict | None:
        result = self._match(frame)
        return result if result is not None and result["confidence"] >= self.threshold else None
