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
) -> Optional[A11yNode]:
    """Melhor nó que casa com needles e/ou filtros nomeados."""
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

    best: Optional[A11yNode] = None
    best_key: Optional[tuple] = None

    for n in nodes:
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
                    # bónus exact
                    if want == _norm(n.text) or want == _norm(n.content_desc):
                        score += 40
                    break
            if not matched and not named:
                continue

        if not needle_list and not named:
            continue

        area = n.area if prefer_smaller else -n.area
        key = (pri, -score, area)
        if best_key is None or key < best_key:
            best_key = key
            best = n

    return best


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


class A11yNavigator:
    """Navegador A11y-first: dump → find → tap (sem frame)."""

    def __init__(
        self,
        *,
        serial: Optional[str] = None,
        adb_path: Optional[str] = None,
        executor: Optional[ActionExecutor] = None,
        log: Optional[Any] = None,
    ) -> None:
        self.serial = serial or _default_serial()
        self.adb_path = adb_path or resolve_adb_path()
        self.ex = executor or ActionExecutor(serial=self.serial, adb_path=self.adb_path)
        self.log = log or (lambda s: print(s, flush=True))
        self.tree: Optional[A11yNode] = None
        self.nodes: list[A11yNode] = []
        self.last_xml: str = ""

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

    def _canonical_hit(self, node: A11yNode) -> tuple[int, int]:
        x1, y1, x2, y2 = node.bounds
        dw, dh = self.ex.device_w, self.ex.device_h
        c1 = map_physical_to_canonical(x1, y1, dw, dh)
        c2 = map_physical_to_canonical(x2, y2, dw, dh)
        return hit_point_in_bounds(c1[0], c1[1], c2[0], c2[1])

    def _tap_node(self, node: A11yNode, *, long: bool = False, why: str = "") -> A11yResult:
        hx, hy = self._canonical_hit(node)
        acao = "long_click" if long else "click"
        self.log(
            f"[a11y] {acao} {node.label()!r} bounds={node.bounds} hit_can=({hx},{hy}) {why}"
        )
        self.ex.execute(
            {
                "acao": acao,
                "coordenadas": {"x": hx, "y": hy},
                "verify": False,
            }
        )
        time.sleep(settle_for_action(acao))
        return A11yResult(ok=True, node=node, action=acao, message=why)

    def click(self, *needles: str, clickable: Optional[bool] = None, **kwargs: Any) -> A11yResult:
        if not self.nodes:
            self.refresh()
        # preferir clicáveis se não especificado
        kw = dict(kwargs)
        if clickable is None and "clickable" not in kw:
            node = find(self.nodes, *needles, clickable=True, **kw)
            if node is None:
                node = find(self.nodes, *needles, **kw)
        else:
            node = find(self.nodes, *needles, clickable=clickable, **kw)
        if node is None:
            return A11yResult(ok=False, action="click", message=f"não encontrado: {needles}")
        return self._tap_node(node, long=False, why="click")

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
    ) -> A11yResult:
        """direction: up|down|left|right (sentido do conteúdo)."""
        if not self.nodes:
            self.refresh()
        d = (direction or "down").strip().lower()
        node = None
        if from_scrollable:
            scrollables = [n for n in self.nodes if n.scrollable and n.area > 5000]
            if scrollables:
                node = max(scrollables, key=lambda n: n.area)
        dw, dh = self.ex.device_w, self.ex.device_h
        if node:
            x1, y1, x2, y2 = node.bounds
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            span_x = int((x2 - x1) * amount)
            span_y = int((y2 - y1) * amount)
        else:
            cx, cy = dw // 2, dh // 2
            span_x = int(dw * amount)
            span_y = int(dh * amount)

        # swipe finger: down content = finger moves up
        if d in ("down", "south"):
            x1, y1, x2, y2 = cx, cy + span_y // 2, cx, cy - span_y // 2
        elif d in ("up", "north"):
            x1, y1, x2, y2 = cx, cy - span_y // 2, cx, cy + span_y // 2
        elif d in ("left", "west"):
            x1, y1, x2, y2 = cx + span_x // 2, cy, cx - span_x // 2, cy
        else:  # right
            x1, y1, x2, y2 = cx - span_x // 2, cy, cx + span_x // 2, cy

        self.log(f"[a11y] scroll {d} phys=({x1},{y1})→({x2},{y2})")
        try:
            if self.ex.backend == "scrcpy" and self.ex.scrcpy.connected:
                self.ex.scrcpy.swipe(x1, y1, x2, y2, duration_ms=280)
            else:
                self.ex.adb.swipe(x1, y1, x2, y2, duration_ms=280)
        except Exception as exc:
            return A11yResult(ok=False, action="scroll", message=str(exc))
        time.sleep(0.35)
        self.refresh()
        return A11yResult(ok=True, node=node, action="scroll", message=d)

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
