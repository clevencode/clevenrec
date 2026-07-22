"""Missão YouVersion → WhatsApp Status via A11y (skills alinhadas).

Negócio (`ambiente-atalho`): intent fixo Éléphant + Sans Bold.
Loop auto: até 5 iterações, grava cache de visão a cada sucesso e acelera.

Uso:
  python -u -m vision_agent.yv_status_a11y_test
  python -u -m vision_agent.yv_status_a11y_test --auto
  python -u -m vision_agent.yv_status_a11y_test --auto --rounds 5
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vision_agent.a11y import A11yNavigator, find
from vision_agent.auto_improve import SHRINK_FACTOR
from vision_agent.config import resolve_adb_path
from vision_agent.env_cache import get_intent, load_cache
from vision_agent.executor import ActionExecutor

VERSE = (
    "Daniel 12:4 S21 — Quant à toi, Daniel, tiens ces paroles cachées et marque "
    "le livre du sceau du secret jusqu'au moment de la fin! Beaucoup seront perplexes, "
    "mais la connaissance augmentera.»\n"
    "https://bible.com/bible/152/dan.12.4.S21"
)

CACHE_PATH = ROOT / "vision_agent" / "step_cache_yv_status_a11y.json"
SOM_CACHE_PATH = ROOT / "vision_agent" / "step_cache_yv_status.json"

# Régua fallback (locate_chip_row sobrescreve)
COLOR_CY = (1150, 1350)
FONT_CY = (1150, 1350)
COLOR_AT_CY = 1248
FONT_AT_CY = 1248
FONT_SCROLL_DIR = "left"

DEFAULT_TIMING: dict[str, float] = {
    "yv_open_s": 1.40,
    "after_long_s": 0.30,
    "wa_open_s": 1.00,
    "after_paste_s": 0.40,
    "after_kb_s": 0.30,
    "after_panel_s": 0.22,
    "after_pick_s": 0.20,
    "after_send_s": 0.55,
    "retry_s": 0.35,
    "scroll_settle_s": 0.12,
}

TIMING_FLOOR: dict[str, float] = {
    "yv_open_s": 0.85,
    "after_long_s": 0.16,
    "wa_open_s": 0.55,
    "after_paste_s": 0.18,
    "after_kb_s": 0.15,
    "after_panel_s": 0.08,
    "after_pick_s": 0.10,
    "after_send_s": 0.35,
    "retry_s": 0.18,
    "scroll_settle_s": 0.06,
}


def _serial() -> str:
    env = os.environ.get("VISION_ADB_SERIAL")
    if env:
        return env
    adb = resolve_adb_path()
    out = subprocess.run([adb, "devices"], capture_output=True, timeout=8).stdout.decode()
    for line in out.splitlines()[1:]:
        if "\tdevice" in line:
            return line.split("\t", 1)[0].strip()
    return "192.168.217.222:5555"


SERIAL = _serial()
ADB = resolve_adb_path()


def sh(*a, timeout=40):
    return subprocess.run([ADB, "-s", SERIAL, *a], capture_output=True, timeout=timeout)


def _sleep(timing: dict[str, float], key: str, default: float = 0.2) -> None:
    time.sleep(float(timing.get(key, default)))


def load_a11y_cache() -> dict[str, Any]:
    if CACHE_PATH.exists():
        return load_cache(CACHE_PATH)
    # bootstrap intent a partir do cache SoM
    intent = {"color": "Éléphant", "font": "Sans Bold", "skip_other_styles": True}
    if SOM_CACHE_PATH.exists():
        try:
            si = get_intent(load_cache(SOM_CACHE_PATH))
            intent = {
                "color": si.color,
                "font": si.font,
                "skip_other_styles": si.skip_other_styles,
                "anticipation": si.anticipation,
            }
        except Exception:
            pass
    return {
        "mission": "yv_status_a11y",
        "version": 1,
        "device_profile": "720x1600",
        "intent": intent,
        "timing": dict(DEFAULT_TIMING),
        "vision": {},
        "history": [],
        "auto_improve": {},
    }


def save_a11y_cache(cache: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def shrink_a11y_timing(timing: dict[str, float], *, factor: float = SHRINK_FACTOR) -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v in timing.items():
        floor = TIMING_FLOOR.get(k, 0.05)
        out[k] = max(floor, round(float(v) * factor, 3))
    return out


def _hit_from_nav(nav: A11yNavigator, *needles: str) -> Optional[dict[str, Any]]:
    want = {n.lower() for n in needles}
    for h in reversed(nav.visual_hits):
        lab = (h.get("label") or "").lower()
        if lab in want or any(w in lab for w in want):
            return h
    return None


def merge_vision_cache(cache: dict[str, Any], nav: A11yNavigator, *, round_i: int, elapsed: float, ok: bool) -> None:
    """Grava hits / plano / antecedência no cache de visão."""
    vision = cache.setdefault("vision", {})
    intent = get_intent(cache)

    def pack(key: str, needles: tuple[str, ...], prefer: str) -> None:
        h = _hit_from_nav(nav, *needles)
        located = None
        if prefer in ("font", "color"):
            located = nav.locate_chip_row(prefer)
        prev = dict(vision.get(key) or {})
        entry: dict[str, Any] = {
            "round": round_i,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        # preservar hit bom se esta iteração não o capturou (miss parcial)
        if h:
            entry.update(
                {
                    "label": h.get("label"),
                    "hit_can": h.get("hit_can"),
                    "bounds": h.get("bounds"),
                    "cy": h.get("cy"),
                    "cx": h.get("cx"),
                    "in_plane": h.get("in_plane"),
                }
            )
        else:
            for k in ("label", "hit_can", "bounds", "cy", "cx", "in_plane", "cy_band", "at_cy", "plane", "pitch"):
                if k in prev:
                    entry[k] = prev[k]
        if located:
            lo, hi, plane = located
            entry["cy_band"] = [lo, hi]
            entry["at_cy"] = plane.cy
            entry["plane"] = plane.as_dict()
            pitch = nav.plane_chip_pitch(plane)
            if pitch:
                entry["pitch"] = round(pitch, 1)
        elif prev.get("cy_band"):
            entry.setdefault("cy_band", prev["cy_band"])
            entry.setdefault("at_cy", prev.get("at_cy"))
            entry.setdefault("plane", prev.get("plane"))
            entry.setdefault("pitch", prev.get("pitch"))
        if key == "color":
            entry["direction"] = "left"
        elif key == "font":
            entry["direction"] = "left"
        vision[key] = entry

    pack("color", (intent.color, "éléphant", "elephant"), "color")
    pack("font", (intent.font, "sans bold"), "font")
    pack("palette", ("couleur de fond",), "auto")
    pack("envoyer", ("envoyer",), "auto")

    ant = cache.setdefault("intent", {}).setdefault("anticipation", {})
    for kind in ("color", "font"):
        v = vision.get(kind) or {}
        if not v:
            continue
        ant[kind] = {
            "direction": v.get("direction", "left"),
            "scroll_bursts": max(1, 6 - round_i),
            "step_px": int(v.get("pitch") or 112),
            "row_cy": v.get("cy_band") or ([v["cy"] - 70, v["cy"] + 70] if v.get("cy") else None),
            "land_markers": [intent.color if kind == "color" else intent.font],
            "hit_can": v.get("hit_can"),
        }

    hist = cache.setdefault("history", [])
    hist.append(
        {
            "round": round_i,
            "ok": ok,
            "elapsed_s": round(elapsed, 2),
            "hits": len(nav.visual_hits),
            "timing": dict(cache.get("timing") or {}),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
    )
    # manter histórico curto
    cache["history"] = hist[-20:]
    cache["notes"] = (
        f"a11y round{round_i} {'OK' if ok else 'FAIL'} {elapsed:.1f}s "
        f"hits={len(nav.visual_hits)}"
    )


def published(nav: A11yNavigator) -> bool:
    nav.refresh()
    blob = nav.labels_blob()
    if re.search(r"statut\s+\d+\s+sur\s+\d+", blob):
        return True
    if "vu par" in blob or "viewed by" in blob:
        return True
    if "envoyer" in blob and ("ecrivez" in blob or "écrivez" in blob):
        return False
    return any(k in blob for k in ("instantane", "instantané", "partage", "mon statut", "mes mises"))


def click_scroll_verified(
    nav: A11yNavigator,
    *needles: str,
    max_scrolls: int = 14,
    direction: str = "left",
    at_cy: int | None = None,
    cy_band: tuple[int, int] | None = None,
    expect_gone: bool = False,
    prefer_plane: str = "auto",
    vision_hint: Optional[dict[str, Any]] = None,
) -> bool:
    """Tab-first; cache hit → tap directo; senão scroll_seek no plano."""
    if vision_hint:
        if vision_hint.get("cy_band"):
            cy_band = tuple(vision_hint["cy_band"])  # type: ignore[assignment]
        if vision_hint.get("at_cy") is not None:
            at_cy = int(vision_hint["at_cy"])
        if vision_hint.get("direction"):
            direction = str(vision_hint["direction"])

    if prefer_plane in ("font", "color"):
        located = nav.locate_chip_row(prefer_plane)
        if located is not None:
            lo, hi, plane = located
            cy_band = (lo, hi)
            at_cy = plane.cy
            print(
                f"[vision] chip row → band=({lo},{hi}) cy={at_cy} kind={plane.kind}",
                flush=True,
            )

    cy_min = cy_band[0] if cy_band else None
    cy_max = cy_band[1] if cy_band else None
    if cy_band:
        nav.infer_plane(cy_band[0], cy_band[1], prefer=prefer_plane)

    # atalho: hit_can do cache (antecedência)
    hit_can = (vision_hint or {}).get("hit_can")
    if hit_can and len(hit_can) == 2:
        nav.refresh()
        # se label já visível, preferir tab a11y
        if not any(
            (n.text or n.content_desc or "").strip().lower() == needles[0].lower()
            for n in nav.nodes
            if cy_min is None or cy_min <= n.cy <= (cy_max or 10**9)
        ):
            print(f"[cache] try hit_can {hit_can} for {needles[0]!r}", flush=True)
            nav.ex.execute(
                {
                    "acao": "click",
                    "coordenadas": {"x": int(hit_can[0]), "y": int(hit_can[1])},
                    "verify": False,
                }
            )
            time.sleep(0.2)
            nav.refresh()
            blob = nav.labels_blob()
            # se ainda no painel e intent pode ter sido aplicado, ok se label mudou pouco
            if prefer_plane == "color" and nav.has("terminé", "termine"):
                # verificar se chip intent está seleccionado / presente
                if any(w.lower() in blob for w in needles):
                    return True

    def try_click() -> bool:
        nav.refresh()
        if prefer_plane in ("font", "color"):
            located = nav.locate_chip_row(prefer_plane)
            if located is not None:
                nonlocal cy_band, cy_min, cy_max, at_cy
                lo, hi, plane = located
                cy_band = (lo, hi)
                cy_min, cy_max = lo, hi
                at_cy = plane.cy
        if cy_band:
            nav.infer_plane(cy_band[0], cy_band[1], prefer=prefer_plane)
        for n in nav.nodes:
            if cy_min is not None and n.cy < cy_min:
                continue
            if cy_max is not None and n.cy > cy_max:
                continue
            lab = (n.text or n.content_desc or "").strip().lower()
            for want in needles:
                if lab == want.lower().strip():
                    before = nav.labels_blob()
                    before_fr = nav.grab_frame()
                    nav._tap_node(n, why=f"exact-{want}")
                    time.sleep(0.2)
                    nav.refresh()
                    after_fr = nav.grab_frame()
                    ratio = 0.0
                    if before_fr is not None and after_fr is not None:
                        try:
                            from vision_agent.filter import change_ratio

                            ratio = change_ratio(before_fr, after_fr)
                        except Exception:
                            pass
                    print(
                        f"[vision] tap {want!r} ratio={ratio:.4f} "
                        f"label_chg={nav.labels_blob() != before} "
                        f"in_plane={nav.hit_in_plane(n)}",
                        flush=True,
                    )
                    if expect_gone:
                        return want.lower() not in nav.labels_blob() or nav.labels_blob() != before
                    if ratio < 0.001 and nav.labels_blob() == before:
                        print("[vision] tap sem efeito — bias retry no plano", flush=True)
                        for bx, by in ((0.35, 0.5), (0.65, 0.5), (0.5, 0.35)):
                            nav._tap_node(n, why=f"bias-{want}", bias_x=bx, bias_y=by)
                            time.sleep(0.15)
                            nav.refresh()
                            if nav.labels_blob() != before:
                                return True
                        return False
                    return True
        r = nav.click_verified(
            *needles,
            cy_min=cy_min,
            cy_max=cy_max,
            max_tries=1,
            min_ratio=0.001,
        )
        return bool(r.ok)

    if try_click():
        return True

    # tab miss → seek directo (evitar 2× verified miss vazios — relatório rounds)
    print(f"[vision] tab ausente → scroll_seek {needles[0]!r}", flush=True)
    seek = nav.scroll_seek(
        *needles,
        direction=direction,
        max_scrolls=max_scrolls,
        at_cy=at_cy,
        cy_band=cy_band,
        prefer_plane=prefer_plane,
        burst_n=int((vision_hint or {}).get("burst_n") or (3 if prefer_plane == "color" else 0)),
    )
    if seek.ok and seek.node is not None:
        if cy_band:
            nav.infer_plane(cy_band[0], cy_band[1], prefer=prefer_plane)
        nav._tap_node(seek.node, why=f"seek-{needles[0]}")
        time.sleep(0.2)
        nav.refresh()
        return True
    return try_click()


def ensure_composer(nav: A11yNavigator, timing: dict[str, float]) -> bool:
    nav.refresh()
    if nav.has_all("envoyer", "couleur de fond"):
        return True
    if nav.has_all("envoyer", "écrivez") or nav.has_all("envoyer", "ecrivez"):
        return True
    if nav.has("envoyer") and nav.has("sans serif") and (
        nav.has("écrivez") or nav.has("ecrivez") or nav.has("couleur de fond")
    ):
        return True
    sh(
        "shell",
        "am",
        "start",
        "-a",
        "android.intent.action.VIEW",
        "-d",
        "whatsapp://status",
        "com.whatsapp",
    )
    _sleep(timing, "wa_open_s", 1.0)
    nav.refresh()
    r = nav.click_verified(
        "nouveau message de statut",
        "nouveau message de status",
        expect=("écrivez", "ecrivez", "envoyer", "couleur"),
        max_tries=2,
    )
    if not r.ok:
        nav.click("nouveau message de statut", "nouveau message de status")
        _sleep(timing, "wa_open_s", 1.0)
        nav.refresh()
    return nav.has_all("couleur de fond") or (
        nav.has("sans serif") and (nav.has("écrivez") or nav.has("ecrivez"))
    )


def open_font_panel(nav: A11yNavigator, timing: dict[str, float]) -> bool:
    nav.refresh()
    if nav.has("supprimer l’aperçu", "supprimer l'aperçu", "supprimer"):
        print("[vision] dismiss pré-visual de link", flush=True)
        nav.click(
            "supprimer l’aperçu du lien",
            "supprimer l'aperçu du lien",
            "supprimer",
        )
        _sleep(timing, "retry_s", 0.35)
        nav.refresh()

    if nav.has("terminé", "termine") and nav.has(
        "morning breeze", "calistoga", "facebook script", "courier prime", "exo 2"
    ):
        plane_row = nav.locate_chip_row("font")
        if plane_row:
            return plane_row[2].valid or plane_row[2].chip_count >= 3
        return True

    r = nav.click_verified(
        "sans serif",
        "sans-serif",
        cy_max=250,
        expect=(
            "morning breeze",
            "calistoga",
            "facebook script",
            "courier prime",
            "exo 2",
            "courier",
        ),
        absent=("éléphant", "elephant", "scorpion", "soleil"),
        max_tries=3,
    )
    if r.ok:
        located = nav.locate_chip_row("font")
        if located:
            lo, hi, plane = located
            print(f"[vision] font plane band=({lo},{hi}) {plane.as_dict()}", flush=True)
            return plane.valid or plane.chip_count >= 3
        return True

    nav.click("sans serif", "sans-serif", cy_max=250)
    _sleep(timing, "after_panel_s", 0.4)
    nav.refresh()
    blob = nav.labels_blob()
    return any(
        k in blob
        for k in ("morning breeze", "calistoga", "courier prime", "exo 2", "facebook script")
    )


def run_once(
    *,
    cache: dict[str, Any],
    round_i: int = 1,
    save_debug: bool = True,
) -> tuple[bool, float, dict[str, Any]]:
    timing = dict(cache.get("timing") or DEFAULT_TIMING)
    intent = get_intent(cache)
    vision = cache.get("vision") or {}
    # antecedência: cor precisa ≥8 seeks se burst falhar; font ≤4
    # round 5 falhou com max_scrolls=4 na cor (relatório visual)
    max_scrolls_color = max(8, 14 - (round_i - 1))
    max_scrolls_font = max(3, 6 - (round_i - 1))

    print(
        f"RUN round={round_i} intent={intent.color!r}/{intent.font!r} "
        f"scrolls color={max_scrolls_color} font={max_scrolls_font} "
        f"timing.yv_open={timing.get('yv_open_s')}",
        flush=True,
    )

    ex = ActionExecutor(serial=SERIAL)
    assert ex.backend == "scrcpy", getattr(ex.scrcpy, "last_error", None)
    print("device", ex.device_w, ex.device_h, "backend", ex.backend, flush=True)
    nav = A11yNavigator(
        serial=SERIAL,
        adb_path=ADB,
        executor=ex,
        debug_dir=(ROOT / ".screenshots" / "a11y_plane") if save_debug else None,
    )

    t0 = time.time()
    ok_color = ok_font = False

    print("1 YV deeplink", flush=True)
    sh("shell", "am", "force-stop", "com.sirma.mobile.bible.android")
    time.sleep(0.15)
    sh(
        "shell",
        "am",
        "start",
        "-a",
        "android.intent.action.VIEW",
        "-d",
        "https://www.bible.com/bible/152/DAN.12.4.S21",
        "com.sirma.mobile.bible.android",
    )
    _sleep(timing, "yv_open_s", 1.4)
    nav.refresh()
    verse = find(nav.nodes, "quant à toi", "paroles cach", "connaissance", "daniel")
    if verse:
        nav._tap_node(verse, long=True, why="verse-long")
        _sleep(timing, "after_long_s", 0.3)
    try:
        ex.scrcpy.set_clipboard(VERSE, paste=False)
    except Exception as exc:
        print("clipboard warn", exc, flush=True)

    print("2 WA status composer", flush=True)
    if not ensure_composer(nav, timing):
        nav.click("actus", "statuts", "status")
        _sleep(timing, "wa_open_s", 0.8)
        if not ensure_composer(nav, timing):
            print("FAIL composer", flush=True)
            elapsed = time.time() - t0
            return False, elapsed, {"color": False, "font": False, "send": False}

    print("3 paste", flush=True)
    nav.refresh()
    edit = find(nav.nodes, "écrivez", "ecrivez", "edittext", editable=True)
    if edit is None:
        edit = find(nav.nodes, "écrivez un statut", "edittext")
    if edit:
        nav._tap_node(edit, why="edit")
        time.sleep(0.15)
    try:
        ex.scrcpy.set_clipboard(VERSE, paste=True)
    except Exception:
        sh("shell", "input", "keyevent", "279")
    _sleep(timing, "after_paste_s", 0.4)
    sh("shell", "input", "keyevent", "4")
    _sleep(timing, "after_kb_s", 0.3)
    nav.refresh()

    print(f"4 cor {intent.color} (intent + plano)", flush=True)
    r_pal = nav.click_verified(
        "couleur de fond",
        "couleur",
        expect=("terminé", "termine", "éléphant", "elephant", "soleil", "scorpion", "monte"),
        max_tries=2 if round_i > 1 else 3,
    )
    if not r_pal.ok:
        nav.click("couleur de fond")
        _sleep(timing, "after_panel_s", 0.4)
        nav.refresh()
    if nav.has("fermer", "inviter avec un lien"):
        nav.click_verified("fermer", expect=("couleur", "terminé", "éléphant", "envoyer"), max_tries=1)
        _sleep(timing, "retry_s", 0.25)
        nav.click_verified("couleur de fond", expect=("terminé", "éléphant", "soleil"), max_tries=2)
        _sleep(timing, "after_panel_s", 0.3)

    color_needles = (intent.color, intent.color.lower(), "éléphant", "elephant")
    color_hint = dict(vision.get("color") or {})
    color_hint.setdefault("burst_n", 3)
    color_hint.setdefault("direction", "left")
    ok_color = click_scroll_verified(
        nav,
        *color_needles,
        max_scrolls=max_scrolls_color,
        direction=color_hint.get("direction") or "left",
        at_cy=COLOR_AT_CY,
        cy_band=COLOR_CY,
        prefer_plane="color",
        vision_hint=color_hint,
    )
    _sleep(timing, "after_pick_s", 0.2)
    nav.refresh()
    if nav.has("terminé", "termine", "done"):
        nav.click_verified(
            "terminé",
            "termine",
            "done",
            expect=("envoyer", "couleur de fond", "sans serif"),
            max_tries=2,
        )
        _sleep(timing, "after_panel_s", 0.25)
    print(f"COR {'OK' if ok_color else 'WARN'}", flush=True)

    print(f"5 tipografia {intent.font} (intent + plano)", flush=True)
    ensure_composer(nav, timing)
    if not open_font_panel(nav, timing):
        print("WARN font panel não abriu", flush=True)
    else:
        font_needles = (intent.font, intent.font.lower(), "sans bold", "Sans Bold")
        font_hint = dict(vision.get("font") or {})
        font_hint.setdefault("direction", "left")
        font_hint["burst_n"] = 0
        ok_font = click_scroll_verified(
            nav,
            *font_needles,
            max_scrolls=max_scrolls_font,
            direction=font_hint.get("direction") or FONT_SCROLL_DIR,
            at_cy=FONT_AT_CY,
            cy_band=FONT_CY,
            prefer_plane="font",
            vision_hint=font_hint,
        )
        _sleep(timing, "after_pick_s", 0.2)
        nav.refresh()
        if nav.has("terminé", "termine", "done"):
            nav.click_verified(
                "terminé",
                "termine",
                expect=("envoyer", "couleur"),
                max_tries=2,
            )
            _sleep(timing, "after_panel_s", 0.25)
    print(f"FONT {'OK' if ok_font else 'WARN'}", flush=True)

    print("6 Envoyer", flush=True)
    ensure_composer(nav, timing)
    nav.refresh()
    if nav.has("terminé", "termine") and not nav.has("envoyer"):
        nav.click("terminé", "termine")
        _sleep(timing, "retry_s", 0.25)
        nav.refresh()
    if nav.has("écrivez", "ecrivez") and not nav.has("aperçu", "daniel", "bible.com"):
        edit = find(nav.nodes, "écrivez", "edittext", editable=True)
        if edit:
            nav._tap_node(edit, why="re-edit")
            try:
                ex.scrcpy.set_clipboard(VERSE, paste=True)
            except Exception:
                sh("shell", "input", "keyevent", "279")
            _sleep(timing, "after_paste_s", 0.35)
            sh("shell", "input", "keyevent", "4")
            _sleep(timing, "after_kb_s", 0.25)
            nav.refresh()

    ok_send = nav.click_verified(
        "envoyer",
        "send",
        expect=(
            "mes mises",
            "mises à jour",
            "nouveau message",
            "vu par",
            "viewed by",
            "statut",
            "instantané",
            "instantane",
        ),
        absent=("écrivez un statut", "couleur de fond"),
        max_tries=2,
    ).ok
    _sleep(timing, "after_send_s", 0.5)
    nav.refresh()
    if not ok_send:
        ok_send = published(nav) or (
            not nav.has("écrivez un statut") and not nav.has("couleur de fond")
        )
    if not ok_send:
        nav.click("envoyer", "send")
        _sleep(timing, "after_send_s", 0.7)
        ok_send = True
    _sleep(timing, "after_send_s", 0.4)
    ok_pub = published(nav)
    ok = bool(ok_send and ok_pub and ok_color and ok_font)

    elapsed = time.time() - t0
    meta = {
        "color": ok_color,
        "font": ok_font,
        "send": ok_send,
        "published": ok_pub,
        "hits": len(nav.visual_hits),
        "round": round_i,
    }
    print(
        f"FINAL_{'OK' if ok else 'FAIL'} elapsed={elapsed:.1f}s "
        f"color={ok_color} font={ok_font} send={ok_send} published={ok_pub} "
        f"hits={len(nav.visual_hits)}",
        flush=True,
    )

    # sempre gravar visão (mesmo em miss parcial — hits úteis)
    merge_vision_cache(cache, nav, round_i=round_i, elapsed=elapsed, ok=ok)
    save_a11y_cache(cache)
    print(f"[cache] saved → {CACHE_PATH.name}", flush=True)
    return ok, elapsed, meta


def run_auto(*, rounds: int = 5, target_s: float = 85.0) -> int:
    cache = load_a11y_cache()
    if not cache.get("timing"):
        cache["timing"] = dict(DEFAULT_TIMING)
    save_a11y_cache(cache)

    best: Optional[dict[str, Any]] = None
    last_good_timing = copy.deepcopy(cache.get("timing") or DEFAULT_TIMING)

    print(
        f"A11Y_AUTO rounds<={rounds} target<={target_s}s serial={SERIAL} "
        f"cache={CACHE_PATH.name}",
        flush=True,
    )

    for i in range(1, rounds + 1):
        print(f"\n===== A11Y AUTO {i}/{rounds} timing={cache.get('timing')} =====", flush=True)
        ok, elapsed, meta = run_once(
            cache=cache,
            round_i=i,
            save_debug=(i == 1),
        )
        entry = {
            "round": i,
            "ok": ok,
            "elapsed_s": round(elapsed, 2),
            "meta": meta,
            "timing": dict(cache.get("timing") or {}),
        }
        if ok:
            last_good_timing = copy.deepcopy(cache.get("timing") or DEFAULT_TIMING)
            if best is None or elapsed < best["elapsed_s"]:
                best = dict(entry)
            cache["auto_improve"] = {
                "done": elapsed <= target_s,
                "best_elapsed_s": best["elapsed_s"],
                "target_s": target_s,
                "rounds": i,
                "timing": dict(last_good_timing),
            }
            save_a11y_cache(cache)

            if elapsed <= target_s:
                print(f"AUTO DONE {elapsed:.1f}s <= {target_s}s", flush=True)
                return 0

            if i < rounds:
                shrunk = shrink_a11y_timing(cache.get("timing") or DEFAULT_TIMING)
                cache["timing"] = shrunk
                cache["notes"] = (
                    f"a11y auto round{i} ok {elapsed:.1f}s → shrink next={shrunk}"
                )
                save_a11y_cache(cache)
                print(f"AUTO shrink → {shrunk}", flush=True)
        else:
            # restaura timing do último sucesso
            cache["timing"] = copy.deepcopy(last_good_timing)
            save_a11y_cache(cache)
            print("AUTO miss — restore timing", flush=True)

    if best:
        cache["timing"] = copy.deepcopy(best.get("timing") or last_good_timing)
        cache["auto_improve"] = {
            "done": False,
            "best_elapsed_s": best["elapsed_s"],
            "target_s": target_s,
            "rounds": rounds,
            "timing": cache["timing"],
        }
        save_a11y_cache(cache)
        print(f"AUTO best={best['elapsed_s']}s (target {target_s}s)", flush=True)
        return 0
    return 1


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="YV→Status A11y test / auto-improve")
    p.add_argument("--auto", action="store_true", help="até N iterações + cache + shrink")
    p.add_argument("--rounds", type=int, default=5)
    p.add_argument("--target", type=float, default=float(os.environ.get("VISION_AUTO_TARGET_S", "85")))
    args = p.parse_args(argv)

    print("YV_A11Y_VISION_TEST", SERIAL, flush=True)
    cache = load_a11y_cache()
    intent = get_intent(cache)
    print(f"intent color={intent.color!r} font={intent.font!r}", flush=True)

    if args.auto or os.environ.get("VISION_A11Y_AUTO", "").strip() in ("1", "true", "yes"):
        return run_auto(rounds=args.rounds, target_s=args.target)

    ok, elapsed, meta = run_once(cache=cache, round_i=1, save_debug=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
