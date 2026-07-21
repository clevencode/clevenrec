"""Normalização de frames para resolução canônica."""

from __future__ import annotations

import cv2
import numpy as np

from .config import CANONICAL_HEIGHT, CANONICAL_WIDTH


def normalize_frame(
    frame: np.ndarray,
    width: int = CANONICAL_WIDTH,
    height: int = CANONICAL_HEIGHT,
) -> np.ndarray:
    """Redimensiona para width x height (ex.: 1080x1920), BGR."""
    if frame is None or frame.size == 0:
        raise ValueError("Frame vazio.")
    h, w = frame.shape[:2]
    if w == width and h == height:
        return frame
    return cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
