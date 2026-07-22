"""
Precisão de navegabilidade ADB — pilha estilo carro autônomo.

Camadas:
  1. Localização  — mapeamento canônico ↔ físico (sem misturar com vídeo scrcpy)
  2. Percepção    — snap ao nó UI / marca SoM mais próxima
  3. Controlo     — hit-point inset (evita bordas mortas do Android)
  4. Feedback     — settle + verificação de efeito (frame mudou?)
  5. Recuperação  — espiral de micro-offsets se o toque não surtiu efeito
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional, Sequence

import numpy as np

from .config import (
    CANONICAL_HEIGHT,
    CANONICAL_WIDTH,
    HIT_INSET_FRAC,
    HIT_INSET_MAX_PX,
    HIT_INSET_MIN_PX,
    PRECISION_MAX_RETRIES,
    PRECISION_OFFSET_STEP_PX,
    PRECISION_VERIFY_THRESHOLD,
    settle_for_action,
)
from .filter import change_ratio

if TYPE_CHECKING:
    from .som import UiMark


@dataclass(frozen=True)
class TapTarget:
    """Alvo de toque no espaço canônico (1080×1920)."""

    x: int
    y: int
    source: str = "raw"  # raw|mark|snap|offset|inset
    mark_id: Optional[int] = None
    bounds: Optional[tuple[int, int, int, int]] = None

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "x": self.x,
            "y": self.y,
            "source": self.source,
        }
        if self.mark_id is not None:
            d["mark_id"] = self.mark_id
        if self.bounds is not None:
            d["bounds"] = list(self.bounds)
        return d


def clamp_xy(
    x: int,
    y: int,
    w: int = CANONICAL_WIDTH,
    h: int = CANONICAL_HEIGHT,
) -> tuple[int, int]:
    return max(0, min(w - 1, int(x))), max(0, min(h - 1, int(y)))


def map_canonical_to_physical(
    x: int,
    y: int,
    physical_w: int,
    physical_h: int,
    canon_w: int = CANONICAL_WIDTH,
    canon_h: int = CANONICAL_HEIGHT,
) -> tuple[int, int]:
    """
    Canônico → pixels físicos do display (uiautomator / input / scrcpy touch).
    Sempre use o tamanho de `wm size`, nunca a resolução do stream de vídeo.
    """
    nx = int(round(x * physical_w / canon_w))
    ny = int(round(y * physical_h / canon_h))
    return clamp_xy(nx, ny, physical_w, physical_h)


def map_physical_to_canonical(
    x: int,
    y: int,
    physical_w: int,
    physical_h: int,
    canon_w: int = CANONICAL_WIDTH,
    canon_h: int = CANONICAL_HEIGHT,
) -> tuple[int, int]:
    nx = int(round(x * canon_w / max(physical_w, 1)))
    ny = int(round(y * canon_h / max(physical_h, 1)))
    return clamp_xy(nx, ny, canon_w, canon_h)


def hit_point_in_bounds(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    bias_x: float = 0.5,
    bias_y: float = 0.5,
    inset_frac: float = HIT_INSET_FRAC,
    inset_min: int = HIT_INSET_MIN_PX,
    inset_max: int = HIT_INSET_MAX_PX,
) -> tuple[int, int]:
    """
    Ponto seguro dentro do bounds (inset nas bordas).

    bias 0.0 = esquerda/topo, 0.5 = centro, 1.0 = direita/base.
    Elementos largos (linhas de texto) podem usar bias_x≈0.25 para acertar o
    conteúdo em vez da margem vazia.
    """
    w = max(1, x2 - x1)
    h = max(1, y2 - y1)
    ix = int(min(inset_max, max(inset_min, w * inset_frac)))
    iy = int(min(inset_max, max(inset_min, h * inset_frac)))
    # Se o bounds for pequeno, reduz inset para não colapsar
    if w <= inset_min * 2 + 2:
        ix = max(1, w // 4)
    if h <= inset_min * 2 + 2:
        iy = max(1, h // 4)
    inner_w = max(1, w - 2 * ix)
    inner_h = max(1, h - 2 * iy)
    bx = min(1.0, max(0.0, bias_x))
    by = min(1.0, max(0.0, bias_y))
    x = int(round(x1 + ix + bx * (inner_w - 1)))
    y = int(round(y1 + iy + by * (inner_h - 1)))
    return clamp_xy(x, y, CANONICAL_WIDTH, CANONICAL_HEIGHT)


def hit_point_for_mark(
    mark: "UiMark",
    *,
    bias_x: float | None = None,
    bias_y: float | None = None,
) -> tuple[int, int]:
    """Hit-point refinado para uma marca SoM (heurística por classe/aspecto)."""
    w = max(1, mark.x2 - mark.x1)
    h = max(1, mark.y2 - mark.y1)
    cls = (mark.cls or "").lower()
    label = (mark.label or "").lower()

    bx = 0.5 if bias_x is None else bias_x
    by = 0.5 if bias_y is None else bias_y

    if bias_x is None and bias_y is None:
        # Linhas de texto muito largas: toca mais à esquerda (conteúdo)
        if w > h * 4 and ("text" in cls or "textview" in cls or len(label) > 24):
            bx = 0.28
        # EditText: centro vertical, um pouco à esquerda (início do campo)
        if "edittext" in cls:
            bx, by = 0.35, 0.5

    return hit_point_in_bounds(mark.x1, mark.y1, mark.x2, mark.y2, bias_x=bx, bias_y=by)


def _point_in_bounds(x: int, y: int, box: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = box
    return x1 <= x <= x2 and y1 <= y <= y2


def _dist2_to_box(x: int, y: int, box: tuple[int, int, int, int]) -> float:
    x1, y1, x2, y2 = box
    cx = min(max(x, x1), x2)
    cy = min(max(y, y1), y2)
    return float((x - cx) ** 2 + (y - cy) ** 2)


def snap_to_nearest_mark(
    x: int,
    y: int,
    marks: Sequence["UiMark"],
    *,
    max_dist_px: float = 80.0,
    prefer_containing: bool = True,
) -> Optional[TapTarget]:
    """
    Snap de coordenada livre → marca SoM mais próxima.
    Preferência: bounds que contém o ponto; senão distância ao retângulo.
    """
    if not marks:
        return None

    containing: list[tuple[float, "UiMark"]] = []
    nearby: list[tuple[float, "UiMark"]] = []
    max_d2 = max_dist_px * max_dist_px

    for m in marks:
        box = (m.x1, m.y1, m.x2, m.y2)
        if prefer_containing and _point_in_bounds(x, y, box):
            # Menor área = alvo mais específico (filho vs pai)
            containing.append((float(m.area), m))
            continue
        d2 = _dist2_to_box(x, y, box)
        if d2 <= max_d2:
            nearby.append((d2, m))

    chosen: Optional["UiMark"] = None
    if containing:
        containing.sort(key=lambda t: t[0])
        chosen = containing[0][1]
        source = "snap_inside"
    elif nearby:
        nearby.sort(key=lambda t: t[0])
        chosen = nearby[0][1]
        source = "snap_near"
    else:
        return None

    hx, hy = hit_point_for_mark(chosen)
    return TapTarget(
        x=hx,
        y=hy,
        source=source,
        mark_id=chosen.id,
        bounds=(chosen.x1, chosen.y1, chosen.x2, chosen.y2),
    )


def resolve_tap_target(
    decision: dict[str, Any],
    marks: Sequence["UiMark"] | None = None,
    *,
    snap: bool = True,
    max_snap_dist: float = 80.0,
) -> TapTarget:
    """
    Resolve o alvo final de um click/long_click a partir da decisão JSON.

    Prioridade:
      1. `marca` (id SoM) → hit-point inset
      2. coordenadas + snap à marca mais próxima (se marks)
      3. coordenadas brutas
    """
    marks = marks or []
    mark_id = decision.get("marca")
    if mark_id is not None and marks:
        try:
            mid = int(mark_id)
        except (TypeError, ValueError):
            mid = None
        if mid is not None:
            for m in marks:
                if m.id == mid:
                    hx, hy = hit_point_for_mark(m)
                    return TapTarget(
                        x=hx,
                        y=hy,
                        source="mark",
                        mark_id=m.id,
                        bounds=(m.x1, m.y1, m.x2, m.y2),
                    )

    coords = decision.get("coordenadas") or {}
    x = int(coords.get("x", CANONICAL_WIDTH // 2))
    y = int(coords.get("y", CANONICAL_HEIGHT // 2))
    x, y = clamp_xy(x, y)

    if snap and marks:
        snapped = snap_to_nearest_mark(x, y, marks, max_dist_px=max_snap_dist)
        if snapped is not None:
            return snapped

    return TapTarget(x=x, y=y, source="raw")


def offset_spiral(
    x: int,
    y: int,
    *,
    step: int = PRECISION_OFFSET_STEP_PX,
    max_rings: int = 3,
) -> Iterable[tuple[int, int]]:
    """
    Espiral de micro-offsets ao redor de (x,y) para retry de toque.
    Ordem: centro → anel 1 (N,E,S,W + diagonais) → anel 2…
    """
    yield clamp_xy(x, y)
    for ring in range(1, max_rings + 1):
        r = ring * step
        # 8 direções por anel, depois pontos extras nos eixos
        dirs = [
            (0, -r),
            (r, 0),
            (0, r),
            (-r, 0),
            (r, -r),
            (r, r),
            (-r, r),
            (-r, -r),
        ]
        if ring >= 2:
            half = r // 2
            dirs.extend(
                [
                    (half, -r),
                    (-half, -r),
                    (r, half),
                    (r, -half),
                    (half, r),
                    (-half, r),
                    (-r, half),
                    (-r, -half),
                ]
            )
        for dx, dy in dirs:
            yield clamp_xy(x + dx, y + dy)


def wait_until_settled(
    grab_fn: Callable[[], np.ndarray],
    *,
    stable_frames: int = 2,
    interval_s: float = 0.08,
    timeout_s: float = 1.2,
    threshold: float = 0.008,
) -> tuple[np.ndarray, bool]:
    """
    Espera a UI estabilizar (mudança entre frames consecutivos < threshold).
    Retorna (último_frame, estabilizou).
    """
    deadline = time.time() + timeout_s
    prev: Optional[np.ndarray] = None
    streak = 0
    last = grab_fn()
    while time.time() < deadline:
        time.sleep(interval_s)
        cur = grab_fn()
        if prev is not None:
            try:
                ratio = change_ratio(prev, cur)
            except ValueError:
                ratio = 1.0
            if ratio < threshold:
                streak += 1
                if streak >= stable_frames:
                    return cur, True
            else:
                streak = 0
        prev = cur
        last = cur
    return last, False


def action_had_effect(
    before: np.ndarray,
    after: np.ndarray,
    *,
    threshold: float = PRECISION_VERIFY_THRESHOLD,
) -> tuple[bool, float]:
    """True se a fração de pixels alterados sugere que o toque surtiu efeito."""
    try:
        ratio = change_ratio(before, after)
    except ValueError:
        return True, 1.0  # shapes diferentes = algo mudou (rotação / resize)
    return ratio >= threshold, ratio


def precise_tap(
    *,
    tap_fn: Callable[[int, int], None],
    grab_fn: Callable[[], np.ndarray] | None,
    canon_x: int,
    canon_y: int,
    physical_w: int,
    physical_h: int,
    verify: bool = True,
    max_retries: int = PRECISION_MAX_RETRIES,
    settle_s: float | None = None,
    acao: str = "click",
) -> dict[str, Any]:
    """
    Toque com malha fechada: executa → settle → verifica → retry com offset.

    tap_fn recebe coordenadas FÍSICAS (wm size).
    grab_fn deve devolver frame canônico (ou mesmo shape before/after).
    """
    settle = settle_for_action(acao) if settle_s is None else settle_s
    attempts: list[dict[str, Any]] = []
    before: Optional[np.ndarray] = None
    if verify and grab_fn is not None:
        try:
            before = grab_fn()
        except Exception:
            before = None
            verify = False

    last_phys = map_canonical_to_physical(canon_x, canon_y, physical_w, physical_h)
    ok_effect = not verify

    for i, (cx, cy) in enumerate(
        offset_spiral(canon_x, canon_y) if verify else [(canon_x, canon_y)]
    ):
        if i > max_retries:
            break
        px, py = map_canonical_to_physical(cx, cy, physical_w, physical_h)
        last_phys = (px, py)
        tap_fn(px, py)
        time.sleep(settle)
        attempt: dict[str, Any] = {
            "i": i,
            "canonical": [cx, cy],
            "physical": [px, py],
        }
        if verify and grab_fn is not None and before is not None:
            try:
                after = grab_fn()
                had, ratio = action_had_effect(before, after)
                attempt["ratio"] = round(ratio, 5)
                attempt["effect"] = had
                attempts.append(attempt)
                if had:
                    ok_effect = True
                    return {
                        "ok": True,
                        "verified": True,
                        "xy_canonical": [cx, cy],
                        "xy_physical": [px, py],
                        "attempts": attempts,
                        "effect_ratio": round(ratio, 5),
                    }
            except Exception as exc:
                attempt["verify_error"] = str(exc)
                attempts.append(attempt)
                # sem verificação → assume ok no 1º toque
                return {
                    "ok": True,
                    "verified": False,
                    "xy_canonical": [cx, cy],
                    "xy_physical": [px, py],
                    "attempts": attempts,
                }
        else:
            attempts.append(attempt)
            return {
                "ok": True,
                "verified": False,
                "xy_canonical": [cx, cy],
                "xy_physical": [px, py],
                "attempts": attempts,
            }

    return {
        "ok": ok_effect,
        "verified": verify,
        "xy_canonical": [canon_x, canon_y],
        "xy_physical": list(last_phys),
        "attempts": attempts,
        "message": "toque sem efeito visual detectável" if verify and not ok_effect else None,
    }


def swipe_path_smooth(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    steps: int = 12,
    ease: str = "ease_in_out",
) -> list[tuple[int, int]]:
    """Trajetória suave (ease) para swipe — menos jitter que interpolação linear."""

    def _ease(t: float) -> float:
        if ease == "linear":
            return t
        # smoothstep
        return t * t * (3.0 - 2.0 * t)

    pts: list[tuple[int, int]] = []
    n = max(1, steps)
    for i in range(n + 1):
        t = _ease(i / n)
        x = int(round(x1 + (x2 - x1) * t))
        y = int(round(y1 + (y2 - y1) * t))
        pts.append((x, y))
    return pts


def confidence_radius(mark: "UiMark") -> float:
    """Raio aproximado do alvo (útil para logging / visualização)."""
    return 0.5 * math.sqrt(max(1, mark.area) / math.pi)
