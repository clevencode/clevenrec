"""Set-of-Mark (SoM): rotula elementos clicáveis da UI para a visão.

Fluxo (boas práticas):
1. Dump uiautomator → candidatos interativos
2. Filtra/deduplica/limita marcas
3. Overlay numerado no frame canônico
4. Catálogo textual [id] → rótulo (canal paralelo à imagem)
5. A IA escolhe `marca`; o executor resolve o centro do bounds
"""

from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Optional

import cv2
import numpy as np

from .config import (
    CANONICAL_HEIGHT,
    CANONICAL_WIDTH,
    SOM_MAX_MARKS,
    SOM_MIN_SIDE,
    resolve_adb_path,
)

_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")

# Cores de alto contraste (BGR) — ciclo para distinguir marcas vizinhas
_MARK_COLORS = [
    (0, 0, 255),
    (255, 128, 0),
    (0, 200, 0),
    (255, 0, 255),
    (0, 220, 255),
    (40, 40, 255),
    (0, 165, 255),
    (180, 105, 255),
]


@dataclass(frozen=True)
class UiMark:
    id: int
    x1: int
    y1: int
    x2: int
    y2: int
    label: str
    cls: str = ""
    aria: str = ""  # text + content-desc + resource-id (navegação tipo TV)

    @property
    def cx(self) -> int:
        return (self.x1 + self.x2) // 2

    @property
    def cy(self) -> int:
        return (self.y1 + self.y2) // 2

    @property
    def area(self) -> int:
        return max(0, self.x2 - self.x1) * max(0, self.y2 - self.y1)

    @property
    def hit(self) -> tuple[int, int]:
        """Hit-point inset (mais seguro que o centro geométrico)."""
        from .precision import hit_point_for_mark

        return hit_point_for_mark(self)

    def as_dict(self) -> dict[str, Any]:
        hx, hy = self.hit
        return {
            "id": self.id,
            "bounds": [self.x1, self.y1, self.x2, self.y2],
            "center": [self.cx, self.cy],
            "hit": [hx, hy],
            "label": self.label,
            "cls": self.cls,
            "aria": self.aria,
        }


def _parse_bounds(raw: str) -> Optional[tuple[int, int, int, int]]:
    m = _BOUNDS_RE.match(raw or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _node_aria(attrib: dict[str, str]) -> str:
    text = (attrib.get("text") or "").strip()
    desc = (attrib.get("content-desc") or "").strip()
    rid = (attrib.get("resource-id") or "").split("/")[-1].strip()
    parts = [p for p in (text, desc, rid) if p]
    return " | ".join(parts)[:120]


def _node_label(attrib: dict[str, str]) -> str:
    text = (attrib.get("text") or "").strip()
    desc = (attrib.get("content-desc") or "").strip()
    rid = (attrib.get("resource-id") or "").split("/")[-1].strip()
    cls = (attrib.get("class") or "").split(".")[-1]
    for candidate in (text, desc, rid, cls):
        if candidate:
            return candidate[:60]
    return "elem"


def _is_interactive(attrib: dict[str, str]) -> bool:
    if (attrib.get("enabled") or "true").lower() == "false":
        return False
    blob = " ".join(
        [
            attrib.get("text") or "",
            attrib.get("content-desc") or "",
            attrib.get("resource-id") or "",
        ]
    ).lower()
    # Ações explícitas (Copy/Share/Send…) — sempre candidatas SoM
    if any(
        k in blob
        for k in (
            "copy",
            "copier",
            "copiar",
            "share",
            "partag",
            "envoyer",
            "send",
            "salvar",
            "save",
            "delete",
            "supprimer",
        )
    ):
        return True
    cls = attrib.get("class") or ""
    if any(
        k in cls
        for k in (
            "Button",
            "ImageButton",
            "CheckBox",
            "Switch",
            "EditText",
            "RadioButton",
            "ToggleButton",
            "FloatingActionButton",
            "Chip",
        )
    ):
        return True
    for key in ("clickable", "long-clickable", "checkable", "focusable"):
        if (attrib.get(key) or "").lower() == "true":
            # focusable sozinho só conta se houver texto/desc útil
            if key == "focusable":
                if (attrib.get("text") or attrib.get("content-desc") or "").strip():
                    return True
                continue
            return True
    return False


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    return inter / float(area_a + area_b - inter + 1e-6)


def dump_ui_xml(adb_path: Optional[str], serial: str, timeout: float = 12.0) -> str:
    """uiautomator dump → XML (string). Vazio se falhar."""
    adb = adb_path or resolve_adb_path()
    try:
        subprocess.run(
            [adb, "-s", serial, "shell", "uiautomator", "dump", "/sdcard/uidump.xml"],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        raw = subprocess.run(
            [adb, "-s", serial, "exec-out", "cat", "/sdcard/uidump.xml"],
            capture_output=True,
            timeout=timeout,
            check=False,
        ).stdout
    except Exception:
        return ""
    text = (raw or b"").decode("utf-8", "replace")
    idx = text.find("<")
    return text[idx:] if idx >= 0 else ""


def extract_marks_from_xml(
    xml_text: str,
    *,
    device_w: int,
    device_h: int,
    canon_w: int = CANONICAL_WIDTH,
    canon_h: int = CANONICAL_HEIGHT,
    max_marks: int = SOM_MAX_MARKS,
    min_side: int = SOM_MIN_SIDE,
) -> list[UiMark]:
    """Extrai marcas no espaço canônico (1080×1920)."""
    if not xml_text or "<" not in xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    sx = canon_w / max(device_w, 1)
    sy = canon_h / max(device_h, 1)
    min_side_dev = max(8, int(min_side * min(device_w / canon_w, device_h / canon_h)))

    candidates: list[tuple[int, int, int, int, str, str, str, int]] = []
    for node in root.iter("node"):
        attrib = node.attrib
        if not _is_interactive(attrib):
            continue
        bounds = _parse_bounds(attrib.get("bounds") or "")
        if not bounds:
            continue
        x1, y1, x2, y2 = bounds
        if (x2 - x1) < min_side_dev or (y2 - y1) < min_side_dev:
            continue
        # ignora quase fullscreen (barra de status / decoradores)
        if (x2 - x1) * (y2 - y1) > 0.85 * device_w * device_h:
            continue
        label = _node_label(attrib)
        aria = _node_aria(attrib)
        cls = (attrib.get("class") or "").split(".")[-1]
        # prioridade: tem texto/desc > botões > área menor (mais específico)
        score = 0
        if (attrib.get("text") or "").strip():
            score += 30
        if (attrib.get("content-desc") or "").strip():
            score += 20
        if "Button" in cls or "EditText" in cls:
            score += 10
        score -= min(20, ((x2 - x1) * (y2 - y1)) // max(1, device_w * device_h // 100))
        candidates.append((x1, y1, x2, y2, label, cls, aria, score))

    # Ordena por posição de leitura (topo→baixo, esq→dir), desempate por score
    candidates.sort(key=lambda c: (c[1] // 12, c[0] // 12, -c[7]))

    kept: list[tuple[int, int, int, int, str, str, str]] = []
    for x1, y1, x2, y2, label, cls, aria, _score in candidates:
        box = (x1, y1, x2, y2)
        if any(_iou(box, (kx1, ky1, kx2, ky2)) > 0.55 for kx1, ky1, kx2, ky2, *_ in kept):
            continue
        kept.append((x1, y1, x2, y2, label, cls, aria))
        if len(kept) >= max_marks:
            break

    marks: list[UiMark] = []
    for i, (x1, y1, x2, y2, label, cls, aria) in enumerate(kept, start=1):
        marks.append(
            UiMark(
                id=i,
                x1=int(round(x1 * sx)),
                y1=int(round(y1 * sy)),
                x2=int(round(x2 * sx)),
                y2=int(round(y2 * sy)),
                label=label,
                cls=cls,
                aria=aria,
            )
        )
    return marks


def catalog_text(marks: list[UiMark]) -> str:
    """Lista textual enviada junto da imagem (canal SoM)."""
    if not marks:
        return "Nenhuma marca disponível — use coordenadas canônicas se necessário."
    lines = ["Elementos rotulados (Set-of-Mark) — escolha pelo número da marca:"]
    for m in marks:
        lines.append(
            f"[{m.id}] {m.label} · {m.cls or 'view'} · centro=({m.cx},{m.cy})"
        )
    return "\n".join(lines)


def render_set_of_mark(
    frame: np.ndarray,
    marks: list[UiMark],
    *,
    thickness: int = 2,
) -> np.ndarray:
    """Desenha caixas + crachá numérico no frame (BGR)."""
    out = frame.copy()
    h, w = out.shape[:2]
    for m in marks:
        color = _MARK_COLORS[(m.id - 1) % len(_MARK_COLORS)]
        x1 = max(0, min(w - 1, m.x1))
        y1 = max(0, min(h - 1, m.y1))
        x2 = max(0, min(w - 1, m.x2))
        y2 = max(0, min(h - 1, m.y2))
        cv2.rectangle(out, (x1, y1), (x2, y2), color, thickness)

        tag = str(m.id)
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = 0.55 if m.id < 10 else 0.48
        (tw, th), _ = cv2.getTextSize(tag, font, scale, 1)
        pad = 3
        bx1, by1 = x1, max(0, y1 - th - pad * 2)
        if by1 < 2:
            by1 = min(h - th - pad * 2, y1 + 2)
        bx2, by2 = min(w - 1, bx1 + tw + pad * 2), min(h - 1, by1 + th + pad * 2)
        cv2.rectangle(out, (bx1, by1), (bx2, by2), color, -1)
        cv2.putText(
            out,
            tag,
            (bx1 + pad, by2 - pad - 1),
            font,
            scale,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    return out


def resolve_mark(
    marks: list[UiMark],
    mark_id: Any,
    *,
    precise: bool = True,
) -> Optional[tuple[int, int]]:
    """
    Retorna hit-point canônico (x,y) da marca, ou None.
    precise=True: inset nas bordas (mais confiável que o centro geométrico).
    """
    try:
        mid = int(mark_id)
    except (TypeError, ValueError):
        return None
    for m in marks:
        if m.id == mid:
            if precise:
                from .precision import hit_point_for_mark

                return hit_point_for_mark(m)
            return m.cx, m.cy
    return None


def find_mark_by_id(marks: list[UiMark], mark_id: Any) -> Optional[UiMark]:
    try:
        mid = int(mark_id)
    except (TypeError, ValueError):
        return None
    for m in marks:
        if m.id == mid:
            return m
    return None


def build_som(
    frame: np.ndarray,
    *,
    serial: str,
    device_w: int,
    device_h: int,
    adb_path: Optional[str] = None,
) -> tuple[np.ndarray, list[UiMark], str]:
    """Pipeline completo: dump → marcas → frame anotado + catálogo."""
    xml_text = dump_ui_xml(adb_path, serial)
    marks = extract_marks_from_xml(
        xml_text,
        device_w=device_w,
        device_h=device_h,
    )
    annotated = render_set_of_mark(frame, marks) if marks else frame
    return annotated, marks, catalog_text(marks)
