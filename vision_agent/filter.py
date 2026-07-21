"""Filtro de estabilidade — só aprova frames com mudança significativa."""

from __future__ import annotations

from typing import Optional, Tuple

import cv2
import numpy as np

from .config import CHANGE_THRESHOLD, DIFF_PIXEL_THRESHOLD


def change_ratio(
    previous: np.ndarray,
    current: np.ndarray,
    pixel_threshold: int = DIFF_PIXEL_THRESHOLD,
) -> float:
    """
    Fração de pixels cuja diferença absoluta (em cinza) supera pixel_threshold.
    Ambos os frames devem ter o mesmo shape.
    """
    if previous.shape != current.shape:
        raise ValueError(
            f"Shapes diferentes: {previous.shape} vs {current.shape}"
        )
    gray_a = cv2.cvtColor(previous, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY)
    diff = cv2.absdiff(gray_a, gray_b)
    changed = np.count_nonzero(diff > pixel_threshold)
    total = diff.size
    if total == 0:
        return 0.0
    return float(changed) / float(total)


class StabilityFilter:
    """Mantém o último frame aprovado e decide se o atual merece análise."""

    def __init__(
        self,
        threshold: float = CHANGE_THRESHOLD,
        pixel_threshold: int = DIFF_PIXEL_THRESHOLD,
    ) -> None:
        self.threshold = threshold
        self.pixel_threshold = pixel_threshold
        self._last_approved: Optional[np.ndarray] = None

    @property
    def last_approved(self) -> Optional[np.ndarray]:
        return self._last_approved

    def should_approve(self, frame: np.ndarray) -> Tuple[bool, float]:
        """
        Retorna (aprovado, ratio).
        O primeiro frame sempre é aprovado.
        """
        if self._last_approved is None:
            self._last_approved = frame.copy()
            return True, 1.0

        ratio = change_ratio(
            self._last_approved,
            frame,
            pixel_threshold=self.pixel_threshold,
        )
        if ratio >= self.threshold:
            self._last_approved = frame.copy()
            return True, ratio
        return False, ratio

    def reset(self) -> None:
        self._last_approved = None
