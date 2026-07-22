"""Navegação ADB por árvore de acessibilidade Android (uiautomator).

Sensor: `uiautomator dump` (AccessibilityNodeInfo).
Actuador: ActionExecutor (scrcpy/ADB) — coords físicas do bounds.

Não requer frame/SoM. Uso:

    nav = A11yNavigator(serial=...)
    nav.refresh()
    nav.click(\"Books\", \"Livres\")
    nav.wait_for(\"EditText\", timeout=5)

CLI:
    python -u -m vision_agent.a11y --dump
    python -u -m vision_agent.a11y --click Books
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vision_agent.config import (
    CANONICAL_HEIGHT,
    CANONICAL_WIDTH,
    resolve_adb_path,
    settle_for_action,
)
from vision_agent.executor import ActionExecutor
from vision_agent.filter import change_ratio
from vision_agent.normalize import normalize_frame
from vision_agent.precision import (
    hit_point_in_bounds,
    map_physical_to_canonical,
)
from vision_agent.som import dump_ui_xml

_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def _norm(s: str) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", s or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.strip().lower())


def _parse_bounds(raw: str) -> Optional[tuple[int, int, int, int]]:
    m = _BOUNDS_RE.match(raw or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _flag(attrib: dict[str, str], key: str) -> bool:
    return (attrib.get(key) or "").lower() == "true"


@dataclass
class A11yNode:
    """Nó da árvore de acessibilidade (bounds em pixels físicos)."""

    text: str = ""
    content_desc: str = ""
    resource_id: str = ""
    class_name: str = ""
    package: str = ""
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    clickable: bool = False
    long_clickable: bool = False
    scrollable: bool = False
    editable: bool = False
    enabled: bool = True
    focused: bool = False
    checked: bool = False
    children: list["A11yNode"] = field(default_factory=list)

    @property
    def cx(self) -> int:
        x1, _, x2, _ = self.bounds
        return (x1 + x2) // 2

    @property
    def cy(self) -> int:
        _, y1, _, y2 = self.bounds
        return (y1 + y2) // 2

    @property
    def area(self) -> int:
        x1, y1, x2, y2 = self.bounds
        return max(0, x2 - x1) * max(0, y2 - y1)

    @property
    def short_class(self) -> str:
        return (self.class_name or "").rsplit(".", 1)[-1]

    @property
    def rid_short(self) -> str:
        return (self.resource_id or "").rsplit("/", 1)[-1]

    def label(self) -> str:
        for candidate in (self.text, self.content_desc, self.rid_short, self.short_class):
            if candidate:
                return candidate[:80]
        return "node"

    def blob(self) -> str:
        return _norm(
            " ".join(
                p
                for p in (
                    self.text,
                    self.content_desc,
                    self.rid_short,
                    self.short_class,
                )
                if p
            )
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "content_desc": self.content_desc,
            "resource_id": self.resource_id,
            "class": self.class_name,
            "package": self.package,
            "bounds": list(self.bounds),
            "clickable": self.clickable,
            "long_clickable": self.long_clickable,
            "scrollable": self.scrollable,
            "editable": self.editable,
            "enabled": self.enabled,
            "label": self.label(),
        }


def _node_from_elem(elem: ET.Element) -> Optional[A11yNode]:
    attrib = elem.attrib
    bounds = _parse_bounds(attrib.get("bounds") or "")
    if not bounds:
        # ainda processa filhos (alguns wrappers sem bounds úteis)
        children = []
        for child in elem:
            n = _node_from_elem(child)
            if n:
                children.append(n)
        if not children:
            return None
        # sintetiza bounds a partir dos filhos
        xs1 = [c.bounds[0] for c in children]
        ys1 = [c.bounds[1] for c in children]
        xs2 = [c.bounds[2] for c in children]
        ys2 = [c.bounds[3] for c in children]
        bounds = (min(xs1), min(ys1), max(xs2), max(ys2))

    node = A11yNode(
        text=(attrib.get("text") or "").strip(),
        content_desc=(attrib.get("content-desc") or "").strip(),
        resource_id=(attrib.get("resource-id") or "").strip(),
        class_name=(attrib.get("class") or "").strip(),
        package=(attrib.get("package") or "").strip(),
        bounds=bounds,
        clickable=_flag(attrib, "clickable"),
        long_clickable=_flag(attrib, "long-clickable"),
        scrollable=_flag(attrib, "scrollable"),
        editable=_flag(attrib, "editable")
        or "EditText" in (attrib.get("class") or ""),
        enabled=(attrib.get("enabled") or "true").lower() != "false",
        focused=_flag(attrib, "focused"),
        checked=_flag(attrib, "checked"),
    )
    for child in elem:
        n = _node_from_elem(child)
        if n:
            node.children.append(n)
    return node


def parse_tree(xml_text: str) -> Optional[A11yNode]:
    if not xml_text or "<" not in xml_text:
        return None
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    # hierarchy ou node raiz
    if root.tag == "hierarchy":
        kids = [_node_from_elem(c) for c in root]
        kids = [k for k in kids if k]
        if not kids:
            return None
        if len(kids) == 1:
            return kids[0]
        xs1 = [k.bounds[0] for k in kids]
        ys1 = [k.bounds[1] for k in kids]
        xs2 = [k.bounds[2] for k in kids]
        ys2 = [k.bounds[3] for k in kids]
        wrap = A11yNode(
            class_name="hierarchy",
            bounds=(min(xs1), min(ys1), max(xs2), max(ys2)),
            children=kids,
        )
        return wrap
    return _node_from_elem(root)


def dump_tree(
    adb_path: Optional[str],
    serial: str,
    *,
    timeout: float = 12.0,
) -> Optional[A11yNode]:
    xml_text = dump_ui_xml(adb_path, serial, timeout=timeout)
    return parse_tree(xml_text)


def flatten(
    tree: Optional[A11yNode],
    *,
    clickable_only: bool = False,
    enabled_only: bool = True,
    min_area: int = 1,
) -> list[A11yNode]:
    out: list[A11yNode] = []

    def walk(n: A11yNode) -> None:
        skip = False
        if n.class_name == "hierarchy" and not n.text and not n.content_desc:
            skip = True
        if enabled_only and not n.enabled:
            skip = True
        if clickable_only and not (
            n.clickable or n.long_clickable or n.editable or n.scrollable
        ):
            skip = True
        if n.area < min_area:
            skip = True
        if not skip:
            out.append(n)
        for c in n.children:
            walk(c)

    if tree:
        walk(tree)
    out.sort(key=lambda n: (n.bounds[1] // 8, n.bounds[0] // 8, n.area))
    return out


def find(
    nodes: Sequence[A11yNode],
    *needles: str,
    text: Optional[str] = None,
    desc: Optional[str] = None,
    rid: Optional[str] = None,
    cls: Optional[str] = None,
    clickable: Optional[bool] = None,
    editable: Optional[bool] = None,
    scrollable: Optional[bool] = None,
    contains: bool = True,
    prefer_smaller: bool = True,
    cy_min: Optional[int] = None,
    cy_max: Optional[int] = None,
) -> Optional[A11yNode]:
    ranked = find_ranked(
        nodes,
        *needles,
        text=text,
        desc=desc,
        rid=rid,
        cls=cls,
        clickable=clickable,
        editable=editable,
        scrollable=scrollable,
        contains=contains,
        prefer_smaller=prefer_smaller,
        cy_min=cy_min,
        cy_max=cy_max,
    )
    return ranked[0] if ranked else None


def find_ranked(
    nodes: Sequence[A11yNode],
    *needles: str,
    text: Optional[str] = None,
    desc: Optional[str] = None,
    rid: Optional[str] = None,
    cls: Optional[str] = None,
    clickable: Optional[bool] = None,
    editable: Optional[bool] = None,
    scrollable: Optional[bool] = None,
    contains: bool = True,
    prefer_smaller: bool = True,
    cy_min: Optional[int] = None,
    cy_max: Optional[int] = None,
) -> list[A11yNode]:
    """Candidatos ordenados (melhor primeiro) — para autocorreção de nó errado."""
    named = []
    if text:
        named.append(("text", _norm(text)))
    if desc:
        named.append(("desc", _norm(desc)))
    if rid:
        named.append(("rid", _norm(rid)))
    if cls:
        named.append(("cls", _norm(cls)))

    needle_list = [_norm(n) for n in needles if n and str(n).strip()]
    scored: list[tuple[tuple, A11yNode]] = []

    for n in nodes:
        if cy_min is not None and n.cy < cy_min:
            continue
        if cy_max is not None and n.cy > cy_max:
            continue
        if clickable is True and not (n.clickable or n.long_clickable):
            continue
        if clickable is False and (n.clickable or n.long_clickable):
            continue
        if editable is True and not n.editable:
            continue
        if scrollable is True and not n.scrollable:
            continue

        blob = n.blob()
        score = 0
        pri = 99

        if named:
            ok_named = True
            for kind, want in named:
                hay = {
                    "text": _norm(n.text),
                    "desc": _norm(n.content_desc),
                    "rid": _norm(n.rid_short + " " + n.resource_id),
                    "cls": _norm(n.short_class + " " + n.class_name),
                }[kind]
                if contains:
                    if want not in hay:
                        ok_named = False
                        break
                else:
                    if want != hay:
                        ok_named = False
                        break
                score += 50
            if not ok_named:
                continue

        if needle_list:
            matched = False
            for i, want in enumerate(needle_list):
                if not want:
                    continue
                if contains:
                    hit = want in blob or want in _norm(n.text) or want in _norm(
                        n.content_desc
                    )
                else:
                    hit = want in (
                        _norm(n.text),
                        _norm(n.content_desc),
                        _norm(n.rid_short),
                        _norm(n.short_class),
                    )
                if hit:
                    matched = True
                    pri = min(pri, i)
                    score += 100 - i
                    if want == _norm(n.text) or want == _norm(n.content_desc):
                        score += 40
                    break
            if not matched and not named:
                continue

        if not needle_list and not named:
            continue

        area = n.area if prefer_smaller else -n.area
        scored.append(((pri, -score, area), n))

    scored.sort(key=lambda t: t[0])
    return [n for _, n in scored]


def _default_serial() -> str:
    env = os.environ.get("VISION_ADB_SERIAL")
    if env:
        return env
    adb = resolve_adb_path()
    try:
        out = subprocess.run(
            [adb, "devices"], capture_output=True, timeout=8
        ).stdout.decode("utf-8", "replace")
        for line in out.splitlines()[1:]:
            if "\tdevice" in line:
                return line.split("\t", 1)[0].strip()
    except Exception:
        pass
    return "192.168.1.161:5555"


@dataclass
class A11yResult:
    ok: bool
    node: Optional[A11yNode] = None
    action: str = ""
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "action": self.action,
            "message": self.message,
            "node": None if self.node is None else self.node.as_dict(),
        }


# Marcadores de plano válido vs ruído (pré-visual / texto)
_FONT_PLANE_MARKS = (
    "morning breeze",
    "calistoga",
    "facebook script",
    "courier",
    "exo 2",
    "sans bold",
    "serif",
)
_COLOR_PLANE_MARKS = (
    "éléphant",
    "elephant",
    "scorpion",
    "soleil",
    "glycine",
    "violine",
    "monte",
)
_NOISE_PLANE_MARKS = (
    "bible.com",
    "aperçu",
    "apercu",
    "supprimer",
    "quant à toi",
    "paroles cach",
    "daniel 12",
)


@dataclass
class VisualPlane:
    """Limites do plano de interação (fila de chips / régua)."""

    x1: int
    y1: int
    x2: int
    y2: int
    kind: str = "unknown"  # font|color|unknown|noise
    labels: list[str] = field(default_factory=list)
    chip_count: int = 0

    @property
    def cx(self) -> int:
        return (self.x1 + self.x2) // 2

    @property
    def cy(self) -> int:
        return (self.y1 + self.y2) // 2

    @property
    def width(self) -> int:
        return max(1, self.x2 - self.x1)

    @property
    def height(self) -> int:
        return max(1, self.y2 - self.y1)

    @property
    def valid(self) -> bool:
        return self.kind in ("font", "color") and self.chip_count >= 2

    def contains_phys(self, x: int, y: int, pad: int = 0) -> bool:
        return (
            self.x1 - pad <= x <= self.x2 + pad
            and self.y1 - pad <= y <= self.y2 + pad
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "bounds": [self.x1, self.y1, self.x2, self.y2],
            "kind": self.kind,
            "chip_count": self.chip_count,
            "labels": self.labels[:12],
            "valid": self.valid,
        }


class A11yNavigator:
    """Navegador A11y-first: dump → find → tap (sem frame)."""

    def __init__(
        self,
        *,
        serial: Optional[str] = None,
        adb_path: Optional[str] = None,
        executor: Optional[ActionExecutor] = None,
        log: Optional[Any] = None,
        debug_dir: Optional[Path] = None,
    ) -> None:
        self.serial = serial or _default_serial()
        self.adb_path = adb_path or resolve_adb_path()
        self.ex = executor or ActionExecutor(serial=self.serial, adb_path=self.adb_path)
        self.log = log or (lambda s: print(s, flush=True))
        self.tree: Optional[A11yNode] = None
        self.nodes: list[A11yNode] = []
        self.last_xml: str = ""
        # aprendizado visual técnico (régua + rastreio)
        self.visual_hits: list[dict[str, Any]] = []
        self.last_plane: Optional[VisualPlane] = None
        self.debug_dir = Path(debug_dir) if debug_dir else None
        self._debug_i = 0

    def labels_blob(self) -> str:
        return " ".join(n.blob() for n in self.nodes)

    def grab_frame(self):
        """Frame BGR normalizado (visão) para change_ratio."""
        import cv2
        import numpy as np

        try:
            if self.ex.backend == "scrcpy" and self.ex.scrcpy.connected:
                fr = self.ex.scrcpy.wait_frame(1.5)
                if fr is not None:
                    return normalize_frame(fr)
        except Exception:
            pass
        try:
            raw = subprocess.run(
                [self.adb_path, "-s", self.serial, "exec-out", "screencap", "-p"],
                capture_output=True,
                timeout=20,
            ).stdout
            img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                return None
            return normalize_frame(img)
        except Exception:
            return None

    def infer_plane(
        self,
        cy_min: int,
        cy_max: int,
        *,
        prefer: str = "auto",
    ) -> VisualPlane:
        """
        Calcula limites do plano de interação na faixa Y.

        Chips clicáveis pequenos → união dos bounds.
        Texto largo / pré-visual → kind=noise (objectivo errado).
        """
        dw, dh = self.ex.device_w, self.ex.device_h
        max_chip_w = int(dw * 0.42)
        max_chip_h = int(dh * 0.10)
        chips: list[A11yNode] = []
        noise_hits = 0
        labels: list[str] = []

        for n in self.nodes:
            if n.cy < cy_min or n.cy > cy_max:
                continue
            lab = (n.text or n.content_desc or "").strip()
            if not lab:
                continue
            low = _norm(lab)
            labels.append(low)
            if any(m in low for m in _NOISE_PLANE_MARKS):
                noise_hits += 1
                continue
            x1, y1, x2, y2 = n.bounds
            w, h = x2 - x1, y2 - y1
            if w > max_chip_w or h > max_chip_h:
                continue
            if not (n.clickable or n.long_clickable):
                # "Aa" preview labels — úteis para Y do plano
                if low in ("aa",) or len(low) <= 3:
                    chips.append(n)
                continue
            chips.append(n)

        blob = " ".join(labels)
        font_score = sum(1 for m in _FONT_PLANE_MARKS if m in blob)
        # "serif" sozinho no botão toolbar não conta — precisa outro marcador
        if font_score == 1 and "serif" in blob and "sans serif" in blob:
            if not any(
                m in blob
                for m in (
                    "morning breeze",
                    "calistoga",
                    "courier",
                    "exo",
                    "facebook",
                    "sans bold",
                )
            ):
                font_score = 0
        color_score = sum(1 for m in _COLOR_PLANE_MARKS if m in blob)

        kind = "unknown"
        if noise_hits >= 1 and font_score == 0 and color_score == 0:
            kind = "noise"
        elif prefer == "font" and font_score >= 1:
            kind = "font"
        elif prefer == "color" and color_score >= 1:
            kind = "color"
        elif font_score >= 2 or (font_score >= 1 and any("breeze" in l or "calistoga" in l for l in labels)):
            kind = "font"
        elif color_score >= 1:
            kind = "color"
        elif noise_hits:
            kind = "noise"

        if chips:
            xs1 = [c.bounds[0] for c in chips]
            ys1 = [c.bounds[1] for c in chips]
            xs2 = [c.bounds[2] for c in chips]
            ys2 = [c.bounds[3] for c in chips]
            plane = VisualPlane(
                x1=max(0, min(xs1) - 8),
                y1=max(0, min(ys1) - 6),
                x2=min(dw, max(xs2) + 8),
                y2=min(dh, max(ys2) + 6),
                kind=kind if kind != "unknown" else ("font" if font_score else "unknown"),
                labels=labels[:16],
                chip_count=len(chips),
            )
        else:
            # fallback: faixa Y completa
            plane = VisualPlane(
                x1=int(dw * 0.05),
                y1=cy_min,
                x2=int(dw * 0.95),
                y2=cy_max,
                kind=kind,
                labels=labels[:16],
                chip_count=0,
            )
        self.last_plane = plane
        return plane

    def annotate_plane(
        self,
        frame,
        plane: VisualPlane,
        *,
        hits: Optional[Sequence[dict[str, Any]]] = None,
        swipe: Optional[tuple[int, int, int, int]] = None,
        title: str = "",
    ):
        """Desenha limites do plano + rastreio de cliques (coords físicas→canónicas)."""
        import cv2

        if frame is None:
            return None
        out = frame.copy()
        h, w = out.shape[:2]
        dw, dh = max(self.ex.device_w, 1), max(self.ex.device_h, 1)

        def phys_to_frame(px: int, py: int) -> tuple[int, int]:
            return int(px * w / dw), int(py * h / dh)

        colors = {
            "font": (80, 200, 80),
            "color": (80, 180, 255),
            "noise": (40, 40, 220),
            "unknown": (180, 180, 80),
        }
        col = colors.get(plane.kind, (200, 200, 200))
        x1, y1 = phys_to_frame(plane.x1, plane.y1)
        x2, y2 = phys_to_frame(plane.x2, plane.y2)
        cv2.rectangle(out, (x1, y1), (x2, y2), col, 2)
        # cruz no centro do plano
        cx, cy = phys_to_frame(plane.cx, plane.cy)
        cv2.drawMarker(out, (cx, cy), col, cv2.MARKER_CROSS, 18, 2)
        label = f"{title or 'plane'} {plane.kind} chips={plane.chip_count}"
        cv2.putText(
            out,
            label,
            (x1, max(18, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            col,
            2,
            cv2.LINE_AA,
        )

        # rastreio de cliques recentes
        for hit in list(hits or self.visual_hits)[-8:]:
            hc = hit.get("hit_can")
            if not hc or len(hc) != 2:
                continue
            # hit_can já é canónico (= frame normalizado)
            hx, hy = int(hc[0]), int(hc[1])
            inside = plane.contains_phys(
                int(hx * dw / CANONICAL_WIDTH),
                int(hy * dh / CANONICAL_HEIGHT),
                pad=12,
            )
            c = (60, 220, 60) if inside else (40, 40, 255)
            cv2.circle(out, (hx, hy), 10, c, 2)
            cv2.circle(out, (hx, hy), 2, c, -1)

        if swipe is not None:
            sx1, sy1, sx2, sy2 = swipe
            a = phys_to_frame(sx1, sy1)
            b = phys_to_frame(sx2, sy2)
            cv2.arrowedLine(out, a, b, (0, 255, 255), 2, tipLength=0.15)

        return out

    def save_plane_debug(
        self,
        frame,
        plane: VisualPlane,
        *,
        swipe: Optional[tuple[int, int, int, int]] = None,
        tag: str = "plane",
    ) -> Optional[Path]:
        if frame is None:
            return None
        import cv2

        out_dir = self.debug_dir
        if out_dir is None:
            out_dir = ROOT / ".screenshots" / "a11y_plane"
        out_dir.mkdir(parents=True, exist_ok=True)
        self._debug_i += 1
        ann = self.annotate_plane(frame, plane, swipe=swipe, title=tag)
        path = out_dir / f"{self._debug_i:03d}-{tag}-{plane.kind}.jpg"
        try:
            cv2.imwrite(str(path), ann)
            self.log(f"[vision] plane debug → {path.name}")
            return path
        except Exception:
            return None

    def locate_chip_row(
        self,
        prefer: str = "font",
        *,
        pad: int = 70,
    ) -> Optional[tuple[int, int, "VisualPlane"]]:
        """
        Localiza a faixa Y real dos chips (fontes/cores) em qualquer sítio do ecrã.

        WhatsApp move a fila: meio (~526) ou fundo (~1248) conforme layout.
        """
        if prefer == "color":
            keys = list(_COLOR_PLANE_MARKS)
        else:
            keys = [
                "sans bold",
                "sans serif",
                "morning breeze",
                "calistoga",
                "facebook script",
                "courier prime",
                "exo 2",
                "serif",
            ]
        cys: list[int] = []
        dw = max(self.ex.device_w, 1)
        for n in self.nodes:
            if not (n.clickable or n.long_clickable):
                continue
            # ignorar botão toolbar tipografia (cy baixo)
            if prefer == "font" and n.cy < 250:
                continue
            x1, _, x2, _ = n.bounds
            if (x2 - x1) > int(dw * 0.42):
                continue
            lab = _norm(n.text or n.content_desc or "")
            if not lab:
                continue
            # exact / contains tipografia
            if prefer == "font":
                if lab in ("sans bold", "sans serif", "serif") or any(
                    k in lab for k in keys if k not in ("serif",)
                ):
                    cys.append(n.cy)
            else:
                if any(k in lab for k in keys):
                    cys.append(n.cy)
        if len(cys) < 2:
            return None
        cys.sort()
        mid = cys[len(cys) // 2]
        lo = max(0, min(cys) - pad)
        hi = min(self.ex.device_h, max(cys) + pad)
        # se espalhado demais, apertar em torno da mediana
        if hi - lo > 220:
            lo, hi = max(0, mid - pad), min(self.ex.device_h, mid + pad)
        plane = self.infer_plane(lo, hi, prefer=prefer)
        self.log(
            f"[vision] locate_chip_row prefer={prefer} band=({lo},{hi}) "
            f"plane={plane.kind} chips={plane.chip_count}"
        )
        return lo, hi, plane

    def plane_chip_pitch(self, plane: VisualPlane) -> Optional[float]:
        """Pitch mediano entre chips no plano (régua tátil sob medida)."""
        cxs: list[int] = []
        dw = max(self.ex.device_w, 1)
        for n in self.nodes:
            if not plane.contains_phys(n.cx, n.cy, pad=10):
                continue
            if not (n.clickable or n.long_clickable):
                continue
            w = n.bounds[2] - n.bounds[0]
            if w > int(dw * 0.42) or w < 20:
                continue
            cxs.append(n.cx)
        cxs = sorted(set(cxs))
        if len(cxs) < 2:
            return None
        diffs = [cxs[i + 1] - cxs[i] for i in range(len(cxs) - 1) if cxs[i + 1] - cxs[i] >= 24]
        if not diffs:
            return None
        diffs.sort()
        return float(diffs[len(diffs) // 2])

    def amount_from_plane(
        self,
        plane: VisualPlane,
        *,
        default: float = 0.42,
        chips_per_swipe: float = 2.4,
    ) -> float:
        """Converte pitch do plano em fracção de swipe (navegacao-tab).

        Relatório visual: 1.4 chips/gesto era demasiado curto (~5 scrolls até Éléphant).
        Default ~2.4 chips por gesto.
        """
        pitch = self.plane_chip_pitch(plane)
        if pitch is None or plane.width <= 0:
            return default
        frac = (chips_per_swipe * pitch) / float(plane.width)
        return max(0.36, min(0.62, frac))

    def swipe_burst(
        self,
        direction: str,
        *,
        n: int,
        plane: VisualPlane,
        amount: float = 0.48,
        settle_s: float = 0.09,
    ) -> None:
        """Antecedência: N swipes no plano sem dump a cada gesto (mais rápido)."""
        if n <= 0 or plane is None:
            return
        d = (direction or "left").lower()
        pad = max(8, int(plane.width * 0.06))
        x_lo, x_hi = plane.x1 + pad, plane.x2 - pad
        cy = plane.cy
        cx = plane.cx
        span = int(plane.width * amount)
        self.log(
            f"[vision] burst {d} n={n} amount={amount:.2f} plane={plane.kind}"
        )
        for i in range(n):
            if d in ("left", "west"):
                sx1, sy1 = min(x_hi, cx + span // 2), cy
                sx2, sy2 = max(x_lo, cx - span // 2), cy
            else:
                sx1, sy1 = max(x_lo, cx - span // 2), cy
                sx2, sy2 = min(x_hi, cx + span // 2), cy
            try:
                if self.ex.backend == "scrcpy" and self.ex.scrcpy.connected:
                    self.ex.scrcpy.swipe(sx1, sy1, sx2, sy2, duration_ms=220)
                else:
                    self.ex.adb.swipe(sx1, sy1, sx2, sy2, duration_ms=220)
            except Exception as exc:
                self.log(f"[vision] burst swipe fail {exc}")
                break
            time.sleep(settle_s)
        self.refresh()

    def has_all(self, *needles: str, **kwargs: Any) -> bool:
        return all(self.has(n, **kwargs) for n in needles)

    def hit_in_plane(
        self,
        node: A11yNode,
        plane: Optional[VisualPlane] = None,
    ) -> bool:
        pl = plane or self.last_plane
        if pl is None:
            return True
        return pl.contains_phys(node.cx, node.cy, pad=20)

    def refresh(self, *, clickable_only: bool = False) -> list[A11yNode]:
        self.last_xml = dump_ui_xml(self.adb_path, self.serial)
        self.tree = parse_tree(self.last_xml)
        self.nodes = flatten(self.tree, clickable_only=clickable_only)
        self.log(f"[a11y] refresh nodes={len(self.nodes)}")
        return self.nodes

    def has(self, *needles: str, **kwargs: Any) -> bool:
        if not self.nodes:
            self.refresh()
        return find(self.nodes, *needles, **kwargs) is not None

    def exists(self, *needles: str, **kwargs: Any) -> bool:
        return self.has(*needles, **kwargs)

    def _expect_ok(
        self,
        expect: Optional[Sequence[str]],
        absent: Optional[Sequence[str]],
    ) -> bool:
        if expect and not any(self.has(e) for e in expect):
            return False
        if absent and any(self.has(a) for a in absent):
            return False
        return True

    def _canonical_hit(
        self,
        node: A11yNode,
        *,
        bias_x: float = 0.5,
        bias_y: float = 0.5,
    ) -> tuple[int, int]:
        x1, y1, x2, y2 = node.bounds
        dw, dh = self.ex.device_w, self.ex.device_h
        c1 = map_physical_to_canonical(x1, y1, dw, dh)
        c2 = map_physical_to_canonical(x2, y2, dw, dh)
        return hit_point_in_bounds(
            c1[0], c1[1], c2[0], c2[1], bias_x=bias_x, bias_y=bias_y
        )

    def _tap_node(
        self,
        node: A11yNode,
        *,
        long: bool = False,
        why: str = "",
        bias_x: float = 0.5,
        bias_y: float = 0.5,
    ) -> A11yResult:
        hx, hy = self._canonical_hit(node, bias_x=bias_x, bias_y=bias_y)
        acao = "long_click" if long else "click"
        in_plane = self.hit_in_plane(node)
        self.log(
            f"[a11y] {acao} {node.label()!r} bounds={node.bounds} "
            f"hit_can=({hx},{hy}) bias=({bias_x:.2f},{bias_y:.2f}) "
            f"in_plane={in_plane} {why}"
        )
        self.ex.execute(
            {
                "acao": acao,
                "coordenadas": {"x": hx, "y": hy},
                "verify": False,
            }
        )
        time.sleep(settle_for_action(acao))
        self.visual_hits.append(
            {
                "label": node.label(),
                "bounds": list(node.bounds),
                "hit_can": [hx, hy],
                "bias": [bias_x, bias_y],
                "cy": node.cy,
                "cx": node.cx,
                "area": node.area,
                "why": why,
                "in_plane": in_plane,
                "plane": None if self.last_plane is None else self.last_plane.as_dict(),
            }
        )
        return A11yResult(ok=True, node=node, action=acao, message=why)

    def dismiss_light(self) -> None:
        self.refresh()
        if self.has("fermer", "close", "annuler", "cancel"):
            self.click("fermer", "close", "annuler", "cancel")
            time.sleep(0.3)
            return
        # BACK barato
        subprocess.run(
            [self.adb_path, "-s", self.serial, "shell", "input", "keyevent", "4"],
            capture_output=True,
            timeout=8,
        )
        time.sleep(0.35)

    def click(
        self,
        *needles: str,
        clickable: Optional[bool] = None,
        expect: Optional[Sequence[str]] = None,
        absent: Optional[Sequence[str]] = None,
        max_tries: int = 1,
        cy_min: Optional[int] = None,
        cy_max: Optional[int] = None,
        **kwargs: Any,
    ) -> A11yResult:
        if expect or absent or max_tries > 1:
            return self.click_verified(
                *needles,
                clickable=clickable,
                expect=expect,
                absent=absent,
                max_tries=max_tries,
                cy_min=cy_min,
                cy_max=cy_max,
                **kwargs,
            )
        if not self.nodes:
            self.refresh()
        kw = dict(kwargs)
        kw.setdefault("cy_min", cy_min)
        kw.setdefault("cy_max", cy_max)
        if clickable is None and "clickable" not in kw:
            node = find(self.nodes, *needles, clickable=True, **kw)
            if node is None:
                node = find(self.nodes, *needles, **kw)
        else:
            node = find(self.nodes, *needles, clickable=clickable, **kw)
        if node is None:
            return A11yResult(ok=False, action="click", message=f"não encontrado: {needles}")
        return self._tap_node(node, long=False, why="click")

    def click_verified(
        self,
        *needles: str,
        expect: Optional[Sequence[str]] = None,
        absent: Optional[Sequence[str]] = None,
        max_tries: int = 3,
        clickable: Optional[bool] = None,
        cy_min: Optional[int] = None,
        cy_max: Optional[int] = None,
        min_ratio: float = 0.002,
        **kwargs: Any,
    ) -> A11yResult:
        """
        Click com verificação visual/técnica + autocorreção de nó.

        Parâmetros visuais: bounds, hit inset, bias, change_ratio, cy band.
        Se o ecrã não cumprir expect/absent → tenta próximo candidato / bias.
        """
        biases = [(0.5, 0.5), (0.35, 0.5), (0.65, 0.5), (0.5, 0.4), (0.5, 0.6)]
        last = A11yResult(ok=False, action="click_verified", message="fail")

        for attempt in range(max_tries):
            self.refresh()
            kw = dict(kwargs)
            if cy_min is not None:
                kw["cy_min"] = cy_min
            if cy_max is not None:
                kw["cy_max"] = cy_max
            if clickable is None:
                cands = find_ranked(self.nodes, *needles, clickable=True, **kw)
                if not cands:
                    cands = find_ranked(self.nodes, *needles, **kw)
            else:
                cands = find_ranked(self.nodes, *needles, clickable=clickable, **kw)

            if not cands:
                self.log(f"[a11y] verified miss needles={needles} try={attempt}")
                last = A11yResult(
                    ok=False, action="click_verified", message=f"não encontrado: {needles}"
                )
                continue

            for ci, node in enumerate(cands[:4]):
                before_blob = self.labels_blob()
                before_fr = self.grab_frame()
                for bx, by in biases[: 2 if ci == 0 else 3]:
                    self._tap_node(
                        node,
                        why=f"verified/{needles[0]}/c{ci}/b{bx}",
                        bias_x=bx,
                        bias_y=by,
                    )
                    time.sleep(0.15)
                    self.refresh()
                    ratio = 0.0
                    if before_fr is not None:
                        after_fr = self.grab_frame()
                        if after_fr is not None:
                            try:
                                ratio = change_ratio(before_fr, after_fr)
                            except Exception:
                                ratio = 0.0
                    after_blob = self.labels_blob()
                    label_changed = after_blob != before_blob
                    expect_ok = self._expect_ok(expect, absent)
                    self.log(
                        f"[a11y] verify label_chg={label_changed} "
                        f"ratio={ratio:.4f} expect_ok={expect_ok} node={node.label()!r}"
                    )
                    if expect or absent:
                        if expect_ok:
                            return A11yResult(
                                ok=True,
                                node=node,
                                action="click_verified",
                                message=f"ok ratio={ratio:.4f}",
                            )
                        # transição forte (ex.: Envoyer) — NÃO BACK (falha-inesperada)
                        if ratio >= 0.25:
                            time.sleep(0.5)
                            self.refresh()
                            if self._expect_ok(expect, absent):
                                return A11yResult(
                                    ok=True,
                                    node=node,
                                    action="click_verified",
                                    message=f"ok delayed ratio={ratio:.4f}",
                                )
                            if absent and not any(self.has(a) for a in absent):
                                return A11yResult(
                                    ok=True,
                                    node=node,
                                    action="click_verified",
                                    message=f"ok absent-cleared ratio={ratio:.4f}",
                                )
                            self.log(
                                "[a11y] ratio alto sem expect exacto — "
                                "aceitar sem dismiss"
                            )
                            return A11yResult(
                                ok=True,
                                node=node,
                                action="click_verified",
                                message=f"ok high-ratio={ratio:.4f}",
                            )
                        self.log(
                            f"[a11y] NÓ ERRADO {node.label()!r} — autocorrect"
                        )
                        self.dismiss_light()
                        self.refresh()
                        break  # próximo candidato
                    # sem expect: aceita mudança visual ou de labels
                    if label_changed or ratio >= min_ratio:
                        return A11yResult(
                            ok=True,
                            node=node,
                            action="click_verified",
                            message=f"ok ratio={ratio:.4f}",
                        )
                else:
                    continue
                # dismissed after wrong expect — try next cand
            last = A11yResult(
                ok=False,
                action="click_verified",
                message="candidatos esgotados sem pós-condição",
            )
        return last

    def long_click(self, *needles: str, **kwargs: Any) -> A11yResult:
        if not self.nodes:
            self.refresh()
        node = find(self.nodes, *needles, **kwargs)
        if node is None:
            return A11yResult(ok=False, action="long_click", message=f"não encontrado: {needles}")
        return self._tap_node(node, long=True, why="long_click")

    def scroll(
        self,
        direction: str = "down",
        *,
        from_scrollable: bool = True,
        amount: float = 0.35,
        at_cy: Optional[int] = None,
        cy_band: Optional[tuple[int, int]] = None,
        plane: Optional[VisualPlane] = None,
    ) -> A11yResult:
        """direction: up|down|left|right (sentido do conteúdo).

        at_cy / cy_band / plane: régua visual — swipe dentro dos limites do plano.
        """
        if not self.nodes:
            self.refresh()
        d = (direction or "down").strip().lower()
        node = None
        if from_scrollable:
            scrollables = [n for n in self.nodes if n.scrollable and n.area > 5000]
            if cy_band:
                lo, hi = cy_band
                banded = [n for n in scrollables if lo <= n.cy <= hi]
                if banded:
                    scrollables = banded
            if scrollables:
                if d in ("left", "right", "west", "east"):
                    node = min(
                        scrollables,
                        key=lambda n: (n.bounds[3] - n.bounds[1], -n.area),
                    )
                else:
                    node = max(scrollables, key=lambda n: n.area)

        dw, dh = self.ex.device_w, self.ex.device_h
        pl = plane or self.last_plane

        if pl is not None and pl.chip_count >= 1 and pl.kind != "noise":
            # swipe estritamente dentro dos limites do plano
            cx, cy = pl.cx, pl.cy
            span_x = int(pl.width * amount)
            span_y = int(pl.height * max(amount, 0.55))
            # margem interna para não sair do plano
            pad = max(8, int(pl.width * 0.06))
            x_lo, x_hi = pl.x1 + pad, pl.x2 - pad
            y_lo, y_hi = pl.y1 + pad, pl.y2 - pad
        elif node:
            x1, y1, x2, y2 = node.bounds
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            span_x = int((x2 - x1) * amount)
            span_y = int((y2 - y1) * amount)
            x_lo, x_hi, y_lo, y_hi = x1, x2, y1, y2
        else:
            cx, cy = dw // 2, int(at_cy) if at_cy is not None else dh // 2
            if cy_band and at_cy is None:
                cy = (cy_band[0] + cy_band[1]) // 2
            span_x = int(dw * amount)
            span_y = int(dh * amount)
            x_lo, x_hi, y_lo, y_hi = 0, dw, 0, dh

        if at_cy is not None and (pl is None or pl.kind == "noise"):
            cy = int(at_cy)
        elif pl is not None and pl.kind != "noise":
            cy = pl.cy

        if d in ("down", "south"):
            sx1, sy1 = cx, min(y_hi, cy + span_y // 2)
            sx2, sy2 = cx, max(y_lo, cy - span_y // 2)
        elif d in ("up", "north"):
            sx1, sy1 = cx, max(y_lo, cy - span_y // 2)
            sx2, sy2 = cx, min(y_hi, cy + span_y // 2)
        elif d in ("left", "west"):
            sx1, sy1 = min(x_hi, cx + span_x // 2), cy
            sx2, sy2 = max(x_lo, cx - span_x // 2), cy
        else:
            sx1, sy1 = max(x_lo, cx - span_x // 2), cy
            sx2, sy2 = min(x_hi, cx + span_x // 2), cy

        self.log(
            f"[a11y] scroll {d} phys=({sx1},{sy1})→({sx2},{sy2}) cy={cy} "
            f"plane={None if pl is None else pl.kind}"
        )
        # debug visual: limites + seta de swipe + hits
        try:
            fr = self.grab_frame()
            if fr is not None and pl is not None:
                self.save_plane_debug(
                    fr, pl, swipe=(sx1, sy1, sx2, sy2), tag=f"swipe-{d}"
                )
        except Exception:
            pass

        try:
            if self.ex.backend == "scrcpy" and self.ex.scrcpy.connected:
                self.ex.scrcpy.swipe(sx1, sy1, sx2, sy2, duration_ms=280)
            else:
                self.ex.adb.swipe(sx1, sy1, sx2, sy2, duration_ms=280)
        except Exception as exc:
            return A11yResult(ok=False, action="scroll", message=str(exc))
        time.sleep(0.35)
        self.refresh()
        return A11yResult(ok=True, node=node, action="scroll", message=d)

    def band_fingerprint(
        self,
        cy_min: int,
        cy_max: int,
    ) -> list[tuple[int, str]]:
        """Régua visual: (cx, label) na faixa Y — para detectar movimento/erro."""
        items: list[tuple[int, str]] = []
        for n in self.nodes:
            if n.cy < cy_min or n.cy > cy_max:
                continue
            lab = (n.text or n.content_desc or "").strip().lower()
            if lab:
                items.append((n.cx, lab))
        items.sort(key=lambda t: t[0])
        return items

    def band_change_ratio(
        self,
        before,
        after,
        cy_min: int,
        cy_max: int,
    ) -> float:
        """change_ratio só na faixa Y (mapeada ao frame canónico)."""
        if before is None or after is None:
            return 0.0
        h, w = before.shape[:2]
        # bounds físicos → canónicos (frame já é canónico 1080×1920)
        y0 = int(cy_min * h / max(self.ex.device_h, 1))
        y1 = int(cy_max * h / max(self.ex.device_h, 1))
        y0 = max(0, min(h - 1, y0))
        y1 = max(y0 + 1, min(h, y1))
        try:
            return float(change_ratio(before[y0:y1, :], after[y0:y1, :]))
        except Exception:
            try:
                return float(change_ratio(before, after))
            except Exception:
                return 0.0

    @staticmethod
    def _opposite_dir(direction: str) -> str:
        d = (direction or "").lower()
        return {
            "left": "right",
            "right": "left",
            "up": "down",
            "down": "up",
            "west": "east",
            "east": "west",
            "north": "south",
            "south": "north",
        }.get(d, "right" if d == "left" else "left")

    def scroll_seek(
        self,
        *needles: str,
        direction: str = "left",
        max_scrolls: int = 14,
        at_cy: Optional[int] = None,
        cy_band: Optional[tuple[int, int]] = None,
        amount: float = 0.42,
        min_band_ratio: float = 0.008,
        prefer_plane: str = "auto",
        burst_n: int = 0,
    ) -> A11yResult:
        """
        Scroll até o objectivo com correção por visão (plano + rastreio).

        Relatório rounds: paleta no início (ciel/maya) → burst antecipado;
        amount curto (0.28) → 5+ scrolls; tipografia = 1 left.
        """
        cy_min, cy_max = (0, self.ex.device_h)
        if cy_band:
            cy_min, cy_max = cy_band
        want = [_norm(n) for n in needles if n]
        direction = (direction or "left").lower()
        amount_cur = amount
        flipped = False
        burst_done = False
        chips_per = 2.2
        if prefer_plane == "auto":
            if any("bold" in w or w == "sans bold" for w in want):
                prefer_plane = "font"
            elif any(
                w in ("éléphant", "elephant", "scorpion", "soleil") for w in want
            ):
                prefer_plane = "color"
        if prefer_plane == "color":
            chips_per = 2.9
        elif prefer_plane == "font":
            chips_per = 2.2

        early_color = (
            "ciel",
            "bleu maya",
            "monte carlo",
            "emeraude",
            "émeraude",
            "wasabi",
            "jaune curry",
        )
        near_elephant = (
            "etoile",
            "étoile",
            "gris clair",
            "bleu pluie",
            "glycine",
            "ce soir",
            "elephant",
            "éléphant",
        )

        for i in range(max_scrolls + 1):
            self.refresh()
            plane = self.infer_plane(cy_min, cy_max, prefer=prefer_plane)
            self.log(
                f"[vision] plane kind={plane.kind} chips={plane.chip_count} "
                f"bounds=({plane.x1},{plane.y1},{plane.x2},{plane.y2}) "
                f"labels={plane.labels[:6]}"
            )

            hit = find(
                self.nodes,
                *needles,
                cy_min=cy_min,
                cy_max=cy_max,
            )
            if hit is None:
                for n in self.nodes:
                    if cy_min <= n.cy <= cy_max:
                        lab = _norm(n.text or n.content_desc or "")
                        if lab in want:
                            hit = n
                            break
            if hit is not None:
                return A11yResult(
                    ok=True, node=hit, action="scroll_seek", message=f"found@{i}"
                )

            if plane.kind == "noise" or (
                prefer_plane in ("font", "color") and not plane.valid
            ):
                located = self.locate_chip_row(prefer_plane)
                if located is not None:
                    cy_min, cy_max, plane = located
                    cy_band = (cy_min, cy_max)
                    at_cy = plane.cy
                    self.log(
                        f"[vision] CORRIGIR faixa do plano → ({cy_min},{cy_max}) "
                        f"kind={plane.kind}"
                    )
                    continue
                if self.has("supprimer l’aperçu", "supprimer l'aperçu", "supprimer"):
                    self.click(
                        "supprimer l’aperçu du lien",
                        "supprimer l'aperçu du lien",
                        "supprimer",
                    )
                    time.sleep(0.35)
                    self.refresh()
                    located = self.locate_chip_row(prefer_plane)
                    if located is not None:
                        cy_min, cy_max, plane = located
                        cy_band = (cy_min, cy_max)
                        continue
                return A11yResult(
                    ok=False,
                    action="scroll_seek",
                    message=f"plano inválido kind={plane.kind}",
                )

            amount_cur = self.amount_from_plane(
                plane, default=amount_cur, chips_per_swipe=chips_per
            )
            fp = self.band_fingerprint(cy_min, cy_max)
            labels_blob = " ".join(lab for _, lab in fp)

            if (
                prefer_plane == "color"
                and not burst_done
                and any(m in labels_blob for m in early_color)
                and not any(m in labels_blob for m in near_elephant)
            ):
                n_burst = burst_n if burst_n > 0 else 3
                self.log(
                    f"[vision] paleta no INÍCIO → burst×{n_burst} "
                    f"(relatório: Éléphant no fim)"
                )
                self.swipe_burst(
                    direction,
                    n=n_burst,
                    plane=plane,
                    amount=max(amount_cur, 0.48),
                )
                burst_done = True
                continue

            if "sans bold" in want:
                chip_labs = [
                    (n.cx, _norm(n.text or n.content_desc or ""))
                    for n in self.nodes
                    if plane.y1 <= n.cy <= plane.y2
                    and n.clickable
                    and (n.bounds[2] - n.bounds[0]) < int(self.ex.device_w * 0.42)
                ]
                chip_labs.sort()
                if chip_labs and chip_labs[0][1] == "sans serif":
                    if direction != "left":
                        direction = "left"
                        flipped = True
                    amount_cur = max(amount_cur, 0.42)

            before_fr = self.grab_frame()
            before_fp = list(fp)
            use_cy = plane.cy if plane.valid else at_cy
            self.log(
                f"[vision] seek {needles[0]!r} scroll {direction} i={i} "
                f"amount={amount_cur:.2f} plane_cy={use_cy} band={before_fp[:4]}…"
            )
            self.scroll(
                direction,
                at_cy=use_cy,
                cy_band=cy_band,
                amount=amount_cur,
                from_scrollable=False,
                plane=plane,
            )
            time.sleep(0.08)
            after_fr = self.grab_frame()
            after_plane = self.infer_plane(cy_min, cy_max, prefer=prefer_plane)
            after_fp = self.band_fingerprint(cy_min, cy_max)
            ratio = self.band_change_ratio(
                before_fr, after_fr, plane.y1, plane.y2
            )
            moved = after_fp != before_fp or ratio >= min_band_ratio
            self.log(
                f"[vision] band_ratio={ratio:.4f} moved={moved} "
                f"plane_after={after_plane.kind}/{after_plane.chip_count} "
                f"fp_after={after_fp[:4]}"
            )
            if not moved:
                direction = self._opposite_dir(direction)
                amount_cur = min(0.65, amount_cur * 1.2)
                self.log(
                    f"[vision] CORRIGIR movimento → {direction} amount={amount_cur:.2f}"
                )
                flipped = True
                self.scroll(
                    direction,
                    at_cy=after_plane.cy if after_plane.valid else use_cy,
                    cy_band=cy_band,
                    amount=amount_cur,
                    from_scrollable=False,
                    plane=after_plane if after_plane.valid else plane,
                )
                time.sleep(0.08)

        return A11yResult(
            ok=False,
            action="scroll_seek",
            message=f"não encontrado após seek (flipped={flipped})",
        )

    def type_text(
        self,
        text: str,
        *,
        into: Optional[Sequence[str]] = None,
    ) -> A11yResult:
        if not self.nodes:
            self.refresh()
        target = None
        if into:
            target = find(self.nodes, *into, editable=True)
            if target is None:
                target = find(self.nodes, *into)
        if target is None:
            edits = [n for n in self.nodes if n.editable]
            target = edits[0] if edits else None
        if target is None:
            return A11yResult(ok=False, action="type_text", message="sem EditText")
        self._tap_node(target, why="focus-edit")
        time.sleep(0.2)
        self.ex.execute({"acao": "write_text", "texto_input": text, "verify": False})
        time.sleep(0.25)
        return A11yResult(ok=True, node=target, action="type_text", message=text[:40])

    def wait_for(
        self,
        *needles: str,
        timeout: float = 8.0,
        interval: float = 0.45,
        **kwargs: Any,
    ) -> A11yResult:
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            self.refresh()
            last = find(self.nodes, *needles, **kwargs)
            if last is not None:
                return A11yResult(ok=True, node=last, action="wait_for", message="found")
            time.sleep(interval)
        return A11yResult(
            ok=False,
            action="wait_for",
            message=f"timeout {timeout}s: {needles}",
        )

    def back(self) -> A11yResult:
        subprocess.run(
            [self.adb_path, "-s", self.serial, "shell", "input", "keyevent", "4"],
            capture_output=True,
            timeout=8,
        )
        time.sleep(0.4)
        self.refresh()
        return A11yResult(ok=True, action="back")

    def dump_lines(self, *, limit: int = 40, clickable_only: bool = True) -> list[str]:
        if not self.nodes:
            self.refresh(clickable_only=False)
        nodes = self.nodes
        if clickable_only:
            nodes = [
                n
                for n in nodes
                if n.clickable or n.long_clickable or n.editable or n.scrollable
                or n.text
                or n.content_desc
            ]
        lines = []
        for i, n in enumerate(nodes[:limit], 1):
            flags = []
            if n.clickable:
                flags.append("click")
            if n.scrollable:
                flags.append("scroll")
            if n.editable:
                flags.append("edit")
            flag_s = ",".join(flags) or "-"
            lines.append(
                f"[{i:02d}] {n.label()!r} {n.short_class} {flag_s} {n.bounds}"
            )
        return lines


def _cli(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="ADB accessibility navigator")
    p.add_argument("--serial", default=None, help="ADB serial (ou VISION_ADB_SERIAL)")
    p.add_argument("--dump", action="store_true", help="Lista nós A11y")
    p.add_argument("--click", metavar="NEEDLE", help="Clica no primeiro match")
    p.add_argument("--wait", metavar="NEEDLE", help="Espera até o nó aparecer")
    p.add_argument("--scroll", metavar="DIR", choices=["up", "down", "left", "right"])
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--timeout", type=float, default=8.0)
    args = p.parse_args(list(argv) if argv is not None else None)

    nav = A11yNavigator(serial=args.serial)
    print(f"A11Y serial={nav.serial} device={nav.ex.device_w}x{nav.ex.device_h}", flush=True)

    if args.wait:
        r = nav.wait_for(args.wait, timeout=args.timeout)
        print(r.as_dict(), flush=True)
        return 0 if r.ok else 1

    if args.scroll:
        r = nav.scroll(args.scroll)
        print(r.as_dict(), flush=True)
        for line in nav.dump_lines(limit=args.limit):
            print(line, flush=True)
        return 0 if r.ok else 1

    if args.click:
        nav.refresh()
        r = nav.click(args.click)
        print(r.as_dict(), flush=True)
        nav.refresh()
        for line in nav.dump_lines(limit=min(15, args.limit)):
            print(line, flush=True)
        return 0 if r.ok else 1

    # default: dump
    nav.refresh()
    lines = nav.dump_lines(limit=args.limit, clickable_only=True)
    if not lines:
        lines = nav.dump_lines(limit=args.limit, clickable_only=False)
    for line in lines:
        print(line, flush=True)
    print(f"A11Y_DUMP n={len(nav.nodes)} shown={len(lines)}", flush=True)
    return 0 if nav.nodes else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
