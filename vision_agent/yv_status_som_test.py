"""Teste YouVersion → WhatsApp status — navegação tipo comando TV (aria-label).

Fluxo autocorretivo:
  1) Daniel 12:4 (S21) → long-press verso
  2) Copy por aria (ou clipboard forçado)
  3) WA Actus / Status → texto → colar
  4) Cor = 3ª do canto direito da palette + tipografia via marcas
  5) Envoyer → valida ecrã de estatuto publicado

Uso:
  python -u -m vision_agent.yv_status_som_test          # rápido (step_cache)
  python -u -m vision_agent.yv_status_som_test --full   # exploração completa
  python -u -m vision_agent.yv_status_som_test --auto   # autoaperfeiçoamento + gravação

Docs: vision_agent/docs/BASE_NAVEGACAO.md
Cache: vision_agent/step_cache_yv_status.json
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vision_agent.config import JPEG_QUALITY, resolve_adb_path
from vision_agent.executor import ActionExecutor
from vision_agent.normalize import normalize_frame
from vision_agent.remote import RemoteNavigator, find_by_aria
from vision_agent.som import dump_ui_xml

OUT = Path(__file__).resolve().parent / "frames" / "yv_som_test"
OUT.mkdir(parents=True, exist_ok=True)

VERSE_FALLBACK = (
    "Daniel 12:4 S21 — Quant à toi, Daniel, tiens ces paroles cachées et marque "
    "le livre du sceau du secret jusqu'au moment de la fin! Beaucoup seront perplexes, "
    "mais la connaissance augmentera.»\n"
    "https://bible.com/bible/152/dan.12.4.S21"
)


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
    return "LMK410HMYP8HSWCIUO"


SERIAL = _default_serial()
ADB = resolve_adb_path()


def sh(*a, timeout=40):
    return subprocess.run([ADB, "-s", SERIAL, *a], capture_output=True, timeout=timeout)


def save(name: str, frame) -> Path:
    p = OUT / f"{name}.jpg"
    cv2.imwrite(str(p), frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    return p


def grab_norm(ex: ActionExecutor):
    if ex.backend == "scrcpy" and ex.scrcpy.connected:
        fr = ex.scrcpy.wait_frame(2.0)
        if fr is not None:
            return normalize_frame(fr)
    raw = subprocess.run(
        [ADB, "-s", SERIAL, "exec-out", "screencap", "-p"],
        capture_output=True,
        timeout=20,
    ).stdout
    import numpy as np

    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("screencap falhou")
    return normalize_frame(img)


def status_published(nav: RemoteNavigator) -> bool:
    blob = nav.labels()
    # publicado típico: "Statut N sur N" / "Vu par" / viewer
    if re.search(r"statut\s+\d+\s+sur\s+\d+", blob):
        return True
    if "vu par" in blob or "viewed by" in blob:
        return True
    if "envoyer" in blob and "écrivez" in blob:
        return False
    return any(n in blob for n in ("instantané", "partagé", "mon statut"))


def pick_named_in_row(
    nav: RemoteNavigator,
    *names: str,
    row_cy_min: int = 1400,
    row_cy_max: int = 1600,
    max_scrolls: int = 10,
    scroll_sleep: float = 0.28,
    why: str = "named",
    exact_label: bool = True,
) -> bool:
    """
    Desliza a fila horizontal até achar o aria/nome e toca (com centragem).
    exact_label=True: só aceita label normalizado igual ao nome (máxima precisão).
    """
    from vision_agent.precision import map_canonical_to_physical
    from vision_agent.remote import _norm

    names_n = [_norm(n) for n in names if n]
    last_fp = ""
    stable = 0
    dir_left = True
    flipped = False

    def _row():
        r = [m for m in nav.marks if row_cy_min < m.cy < row_cy_max and m.area < 80_000]
        r.sort(key=lambda m: m.cx)
        return r

    def _find():
        # 1) label exacto
        for m in _row():
            if _norm(m.label) in names_n:
                return m
        if exact_label:
            return None
        # 2) aria / contains
        for m in _row():
            blob = _norm(f"{m.label} {m.aria}")
            if any(n in blob for n in names_n):
                return m
        return find_by_aria(nav.marks, *names)

    def _center_tap(mark) -> bool:
        m = mark
        if m.cx < 120 or m.cx > 960:
            y = m.cy
            px1, py1 = map_canonical_to_physical(
                m.cx, y, nav.ex.device_w, nav.ex.device_h
            )
            px2, py2 = map_canonical_to_physical(
                540, y, nav.ex.device_w, nav.ex.device_h
            )
            print(f"PICK center {m.label!r} {m.cx}->540", flush=True)
            try:
                if nav.ex.backend == "scrcpy" and nav.ex.scrcpy.connected:
                    nav.ex.scrcpy.swipe(px1, py1, px2, py2, duration_ms=260)
                else:
                    nav.ex.adb.swipe(px1, py1, px2, py2, duration_ms=260)
            except Exception:
                pass
            time.sleep(scroll_sleep)
            nav.refresh(f"{why}-center")
            nav.dismiss_if_needed()
            again = _find()
            if again:
                m = again
        nav.tap(m, why=why, verify=False)
        print(f"PICK OK {why} {m.label!r}", flush=True)
        return True

    hit = _find()
    if hit is not None and row_cy_min <= hit.cy <= row_cy_max:
        return _center_tap(hit)

    for i in range(max_scrolls):
        row = _row()
        fp = "|".join(m.label for m in row)
        if fp and fp == last_fp:
            stable += 1
            if stable >= 2:
                if not flipped:
                    dir_left = not dir_left
                    flipped = True
                    stable = 0
                    print(f"PICK invert dir {'L' if dir_left else 'R'}", flush=True)
                else:
                    break
        else:
            stable = 0
        last_fp = fp
        if len(row) < 2:
            nav.refresh(f"{why}-empty-{i}")
            nav.dismiss_if_needed()
            continue
        y = int((row[0].cy + row[-1].cy) / 2)
        mid = (row[0].cx + row[-1].cx) // 2
        step = 200
        if "elephant" in why or "color" in why:
            step = 280  # Éléphant fica no fim direito — passos maiores
        if "sans-bold" in why or "font-sans" in why:
            step = 260
        if dir_left:
            x1, x2 = mid + step // 2, mid - step // 2
        else:
            x1, x2 = mid - step // 2, mid + step // 2
        px1, py1 = map_canonical_to_physical(x1, y, nav.ex.device_w, nav.ex.device_h)
        px2, py2 = map_canonical_to_physical(x2, y, nav.ex.device_w, nav.ex.device_h)
        print(
            f"PICK scroll {why} i={i} dir={'L' if dir_left else 'R'} {fp[:60]}",
            flush=True,
        )
        try:
            if nav.ex.backend == "scrcpy" and nav.ex.scrcpy.connected:
                nav.ex.scrcpy.swipe(px1, py1, px2, py2, duration_ms=240)
            else:
                nav.ex.adb.swipe(px1, py1, px2, py2, duration_ms=240)
        except Exception as exc:
            print("PICK swipe err", exc, flush=True)
        time.sleep(scroll_sleep)
        nav.refresh(f"{why}-s{i}")
        nav.dismiss_if_needed()
        hit = _find()
        if hit is not None and row_cy_min <= hit.cy <= row_cy_max:
            return _center_tap(hit)

    print(
        f"WARN PICK miss {why} {names} visíveis={[m.label for m in _row()]}",
        flush=True,
    )
    return False


# Posição na fila de cores (fallback): 1 = último do canto direito, 3 = antepenúltimo
COLOR_FROM_RIGHT = 3


def pick_color_from_right(
    nav: RemoteNavigator,
    *,
    index_from_right: int = COLOR_FROM_RIGHT,
    max_scrolls: int = 16,
    scroll_sleep: float = 0.4,
    save_frames: bool = True,
) -> bool:
    """
    Vai até ao fim direito da palette e escolhe a N-ésima cor
    a contar do canto direito (1=última, 3=antepenúltima).
    """
    n = max(1, int(index_from_right))
    last_fp = ""
    stable = 0

    for i in range(max_scrolls):
        row = [
            m
            for m in nav.marks
            if 1400 < m.cy < 1600 and m.area < 80_000
        ]
        row.sort(key=lambda m: m.cx)
        fp = "|".join(m.label for m in row)
        # fim típico WA FR: ... Éléphant | Rose brûlé | Scorpion
        at_end = any(
            k in fp.lower()
            for k in ("scorpion", "rose brûlé", "rose brule", "éléphant", "elephant")
        ) and any(
            k in fp.lower() for k in ("scorpion", "rose")
        )
        if fp and fp == last_fp:
            stable += 1
            if stable >= 2 or (stable >= 1 and at_end):
                break
        else:
            stable = 0
        last_fp = fp

        if len(row) < 2:
            nav.refresh(f"color-end-{i}")
            continue

        y = int((row[0].cy + row[-1].cy) / 2)
        mid = (row[0].cx + row[-1].cx) // 2
        step = 300  # agressivo até ao fim direito
        x1, x2 = mid + step // 2, mid - step // 2
        from vision_agent.precision import map_canonical_to_physical

        px1, py1 = map_canonical_to_physical(x1, y, nav.ex.device_w, nav.ex.device_h)
        px2, py2 = map_canonical_to_physical(x2, y, nav.ex.device_w, nav.ex.device_h)
        print(f"COR scroll fim-direito i={i} seen={fp[:70]}", flush=True)
        try:
            if nav.ex.backend == "scrcpy" and nav.ex.scrcpy.connected:
                nav.ex.scrcpy.swipe(px1, py1, px2, py2, duration_ms=200)
            else:
                nav.ex.adb.swipe(px1, py1, px2, py2, duration_ms=200)
        except Exception as exc:
            print("COR scroll err", exc, flush=True)
        time.sleep(scroll_sleep)
        nav.refresh(f"color-end-{i}")
        nav.dismiss_if_needed()

    row = [m for m in nav.marks if 1400 < m.cy < 1600 and m.area < 80_000]
    row.sort(key=lambda m: m.cx)
    if len(row) < n:
        print(
            f"WARN cor: só {len(row)} swatches, preciso da {n}ª do direito:",
            [m.label for m in row],
            flush=True,
        )
        if not row:
            return False
        mark = row[-1]
    else:
        mark = row[-n]

    if mark.cx > 960:
        y = mark.cy
        from vision_agent.precision import map_canonical_to_physical

        px1, py1 = map_canonical_to_physical(mark.cx, y, nav.ex.device_w, nav.ex.device_h)
        px2, py2 = map_canonical_to_physical(820, y, nav.ex.device_w, nav.ex.device_h)
        try:
            if nav.ex.backend == "scrcpy" and nav.ex.scrcpy.connected:
                nav.ex.scrcpy.swipe(px1, py1, px2, py2, duration_ms=220)
            else:
                nav.ex.adb.swipe(px1, py1, px2, py2, duration_ms=220)
        except Exception:
            pass
        time.sleep(0.25)
        nav.refresh("color-inset")
        row = [m for m in nav.marks if 1400 < m.cy < 1600 and m.area < 80_000]
        row.sort(key=lambda m: m.cx)
        if len(row) >= n:
            mark = row[-n]

    labels = [m.label for m in row]
    print(
        f"COR pick {n}ª do direito -> {mark.label!r} | fila={labels}",
        flush=True,
    )
    nav.tap(mark, why=f"color-right-{n}", verify=False)
    print(f"COR OK {mark.label!r}", flush=True)
    return True


CACHE_PATH = Path(__file__).resolve().parent / "step_cache_yv_status.json"


def load_step_cache() -> dict:
    from vision_agent.env_cache import load_cache

    return load_cache(CACHE_PATH)


def save_step_cache(cache: dict) -> None:
    from vision_agent.env_cache import save_cache

    save_cache(CACHE_PATH, cache)


def _make_pos_cache(cache: dict, nav: RemoteNavigator, ex: ActionExecutor):
    """PosCache de missao: injecta reopen composer (atalho WA)."""
    from vision_agent.env_cache import PosCache

    pos = PosCache(cache, nav, ex, cache_path=CACHE_PATH, serial=SERIAL)

    def reopen() -> bool:
        print("RESUME checkpoint=composer (deeplink)", flush=True)
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
        time.sleep(float(pos.t.get("wa_open_s", 0.4)))
        nav.refresh("resume-st")
        if not nav.has("nouveau message de statut", "mes mises à jour"):
            pos.tap_step("status_tab", "status-tab-resume")
            time.sleep(float(pos.t.get("after_tap_s", 0.16)))
            nav.refresh("resume-st2")
        pos.tap_step("pencil", "pencil-resume")
        time.sleep(float(pos.t.get("after_tap_s", 0.16)) + 0.2)
        nav.refresh("resume-comp")
        nav.dismiss_if_needed()
        ok = pos.in_checkpoint("composer") or nav.has(
            "écrivez", "envoyer", "couleur de fond"
        )
        if ok:
            pos.last_good = "composer"
        return ok

    pos._reopen_composer = reopen
    pos.reopen_status_composer = reopen  # type: ignore[attr-defined]
    return pos



def main_fast() -> tuple[bool, float, dict]:
    """Replay preditivo (skill ambiente-atalho): cache de visão → atalho.

    Returns:
        (ok, elapsed_s, meta) — meta inclui color/font para autoaperfeiçoamento.
    """
    cache = load_step_cache()
    t = cache["timing"]
    st = cache["steps"]
    print(
        "YV_FAST_PREDICT",
        SERIAL,
        CACHE_PATH.name,
        f"v{cache.get('version')}",
        flush=True,
    )

    ex = ActionExecutor(serial=SERIAL)
    assert ex.backend == "scrcpy", ex.scrcpy.last_error
    print("device", ex.device_w, ex.device_h, "backend", ex.backend, flush=True)

    nav = RemoteNavigator(
        ex,
        serial=SERIAL,
        adb_path=ADB,
        grab_fn=lambda: grab_norm(ex),
        save_fn=None,
    )
    pos = _make_pos_cache(cache, nav, ex)
    ok = False
    ok_color = False
    ok_font = False
    elapsed = 0.0

    try:
        t0 = time.time()
        from vision_agent.env_cache import xy_of

        # --- 1) YV atalho deeplink ---
        print("1 YV deeplink", flush=True)
        sh("shell", "am", "force-stop", "com.sirma.mobile.bible.android")
        time.sleep(0.2)
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
        time.sleep(t["yv_open_s"])
        nav.refresh("f01")
        pred = pos.predict("f01")
        pos.last_good = "yv"

        # --- 2) verso + clipboard (atalho predito: skip Copy) ---
        print("2 verse+clipboard", flush=True)
        verse = find_by_aria(nav.marks, *st["verse_long"]["aria"])
        if verse:
            nav.tap(verse, why="verse-long", long=True, verify=False)
            pos.record("verse_long", [int(verse.cx), int(verse.cy)], verse.label)
        else:
            xy = xy_of(st["verse_long"]) or [532, 789]
            if pred.should_shortcut():
                print("ATALHO preditivo verse hit", xy, flush=True)
            pos.tap_xy(xy, "verse-long", long=True)
        time.sleep(t["after_long_s"])
        try:
            ex.scrcpy.set_clipboard(VERSE_FALLBACK, paste=False)
        except Exception as exc:
            print("clipboard warn", exc, flush=True)
        pos.last_good = "verse_long"

        # --- 3) WA status deeplink (predicção status_tab → pencil) ---
        print("3 WA status deeplink", flush=True)
        if not pos.reopen_status_composer():
            pos.retry_from_last("composer", pos.reopen_status_composer, max_tries=2)
        pos.predict("composer")
        time.sleep(0.15)

        # --- 4) paste ---
        print("4 paste", flush=True)

        def do_paste() -> bool:
            nav.refresh("f04")
            pos.predict("f04")
            if not pos.tap_step("edit", "edit"):
                return False
            time.sleep(0.18)
            try:
                ex.scrcpy.set_clipboard(VERSE_FALLBACK, paste=True)
            except Exception:
                sh("shell", "input", "keyevent", "279")
            time.sleep(t["after_paste_s"])
            sh("shell", "input", "keyevent", "4")
            time.sleep(0.2)
            nav.refresh("f04b")
            return nav.has("envoyer", "couleur de fond", "sans serif")

        if not do_paste():
            pos.retry_from_last("paste", do_paste, max_tries=2)
        pos.last_good = "paste"

        # --- 5) cor = intent (Éléphant) via antecedência; sem analisar outras ---
        color_cfg = st.get("color") or {}
        intent = pos.intent
        print(f"5 cor intent={intent.color!r} (antecipacao)", flush=True)

        def do_color() -> bool:
            from vision_agent.env_cache import anticipate_pick, label_matches_intent

            if not pos.ensure_composer():
                return False
            nav.refresh("f05")
            pos.predict("f05")
            if not pos.tap_step("palette", "palette"):
                return False
            time.sleep(t["after_tap_s"] + 0.12)
            nav.refresh("f05b")
            # overlay agenda / notificacao — fechar e retentar palette
            if nav.has("fermer", "inviter avec un lien", "prière", "agenda"):
                print("WARN overlay apos palette — Fermer + retentar", flush=True)
                if nav.has("fermer"):
                    nav.go("fermer", why="dismiss-overlay", max_tries=1)
                else:
                    sh("shell", "input", "keyevent", "4")
                time.sleep(0.35)
                nav.refresh("f05-ov")
                if not pos.ensure_composer():
                    return False
                if not pos.tap_step("palette", "palette-retry"):
                    return False
                time.sleep(t["after_tap_s"] + 0.12)
                nav.refresh("f05b2")
            if not nav.has("terminé", "termine", "monte carlo", "soleil", "éléphant"):
                print("WARN palette nao abriu color_panel", flush=True)
                return False
            pos.predict("f05b")
            ok_c = False
            picked = anticipate_pick(
                nav,
                cache,
                kind="color",
                scroll_sleep=float(t.get("scroll_s", 0.16)),
                why="color-intent",
            )
            if picked is not None and hasattr(picked, "label"):
                pos.record("color", [int(picked.cx), int(picked.cy)], picked.label)
                ok_c = label_matches_intent(
                    picked.label, intent.aria_for("color")
                )
            if not ok_c:
                print("ANTECIP cor miss → fallback from_right=3", flush=True)
                ok_c = pick_color_from_right(
                    nav,
                    index_from_right=int(color_cfg.get("from_right", 3)),
                    max_scrolls=int(color_cfg.get("max_scrolls", 8)),
                    scroll_sleep=float(t.get("scroll_s", 0.16)),
                )
                row = [m for m in nav.marks if 1400 < m.cy < 1600]
                if row:
                    n = int(color_cfg.get("from_right", 3))
                    row.sort(key=lambda m: m.cx)
                    mark = row[-n] if len(row) >= n else row[-1]
                    if label_matches_intent(mark.label, intent.aria_for("color")):
                        pos.record("color", [int(mark.cx), int(mark.cy)], mark.label)
                        ok_c = True
                    else:
                        ok_c = pick_named_in_row(
                            nav,
                            *intent.aria_for("color"),
                            row_cy_min=1400,
                            row_cy_max=1600,
                            max_scrolls=4,
                            scroll_sleep=float(t.get("scroll_s", 0.16)),
                            why="color-elephant",
                            exact_label=True,
                        )
                        if ok_c:
                            m = find_by_aria(nav.marks, *intent.aria_for("color"))
                            if m:
                                pos.record(
                                    "color", [int(m.cx), int(m.cy)], m.label
                                )
            time.sleep(t["after_panel_s"])
            nav.refresh("f05c")
            if nav.has("terminé", "termine", "done"):
                pos.tap_step("termine", "termine-color")
                time.sleep(t["after_panel_s"])
            nav.refresh("f05d")
            # so OK se realmente tocamos Éléphant — nao bastar reabrir composer
            return bool(ok_c) and (
                nav.has("envoyer", "sans serif", "couleur de fond", "sans bold")
                or pos.ensure_composer()
            )

        ok_color = do_color()
        if not ok_color:
            ok_color = pos.retry_from_last("color", do_color, max_tries=2)
        print(f"COR {'OK' if ok_color else 'WARN'} → segue (objetivo=Envoyer)", flush=True)
        pos.last_good = "color" if ok_color else pos.last_good

        # --- 6) tipografia = intent (Sans Bold) via antecedência ---
        font_cfg = st.get("font") or {}
        print(f"6 tipografia intent={intent.font!r} (antecipacao)", flush=True)

        def do_font() -> bool:
            from vision_agent.env_cache import anticipate_pick, label_matches_intent

            if not pos.ensure_composer():
                return False
            nav.refresh("f06")
            pos.predict("f06")
            # se painel de fontes ja aberto, nao retap Sans Serif no meio da fila
            row_blob = " ".join(m.label.lower() for m in nav.marks)
            font_open = any(
                k in row_blob
                for k in ("serif", "sans bold", "calistoga", "courier", "morning", "exo")
            ) and nav.has("terminé", "termine")
            if not font_open:
                if not pos.tap_step("font_aa", "aa"):
                    return False
                time.sleep(t["after_tap_s"] + 0.08)
                nav.refresh("f06b")
                nav.dismiss_if_needed()
                pos.predict("f06b")
                row_blob = " ".join(m.label.lower() for m in nav.marks)
            if any(
                k in row_blob for k in ("éléphant", "elephant", "glycine", "violine")
            ) and not any(
                k in row_blob for k in ("serif", "bold", "calistoga", "courier")
            ):
                print("WARN ainda cores — retap AA", flush=True)
                pos.tap_step("font_aa", "aa-retry")
                time.sleep(t["after_tap_s"])
                nav.refresh("f06b2")
                nav.dismiss_if_needed()
                row_blob = " ".join(m.label.lower() for m in nav.marks)
            ok_f = False
            if any(
                k in row_blob
                for k in ("serif", "bold", "calistoga", "courier", "morning", "exo")
            ):
                picked = anticipate_pick(
                    nav,
                    cache,
                    kind="font",
                    scroll_sleep=float(t.get("scroll_s", 0.16)),
                    why="font-intent",
                )
                if picked is not None and hasattr(picked, "label"):
                    pos.record(
                        "font", [int(picked.cx), int(picked.cy)], picked.label
                    )
                    ok_f = label_matches_intent(
                        picked.label, intent.aria_for("font")
                    )
                if not ok_f:
                    ok_f = pick_named_in_row(
                        nav,
                        *intent.aria_for("font"),
                        row_cy_min=550,
                        row_cy_max=1650,
                        max_scrolls=int(font_cfg.get("max_scrolls", 3)),
                        scroll_sleep=float(t.get("scroll_s", 0.16)),
                        why="font-sans-bold",
                        exact_label=True,
                    )
                    if ok_f:
                        m = find_by_aria(nav.marks, *intent.aria_for("font"))
                        if m:
                            pos.record("font", [int(m.cx), int(m.cy)], m.label)
            else:
                print("WARN fila fontes nao abriu", flush=True)
                return False
            time.sleep(t["after_panel_s"])
            nav.refresh("f06d")
            nav.dismiss_if_needed()
            if nav.has("terminé", "termine", "done"):
                pos.tap_step("termine", "termine-font")
                time.sleep(t["after_panel_s"])
            return ok_f

        ok_font = do_font()
        if not ok_font:
            ok_font = pos.retry_from_last("font", do_font, max_tries=2)
        print(
            f"FONT {'OK' if ok_font else 'WARN'} → prioridade Envoyer",
            flush=True,
        )

        # --- 7) Envoyer (predicção composer_ready → send) ---
        print("7 Envoyer (objetivo final)", flush=True)

        def do_send() -> bool:
            nav.refresh("f07")
            nav.dismiss_if_needed()
            # fechar painel tipografia/cores antes de Envoyer
            if nav.has("terminé", "termine", "done") and not nav.has("envoyer"):
                pos.tap_step("termine", "termine-before-send")
                time.sleep(t["after_panel_s"])
                nav.refresh("f07-term")
            pred_s = pos.predict("f07")
            if pred_s.step == "send" or nav.has("envoyer", "send"):
                pass
            elif not pos.ensure_composer():
                return False
            # se texto vazio apos recover — recolar
            if nav.has("écrivez un statut") and not nav.has("aperçu", "daniel", "bible.com"):
                print("WARN composer vazio — recolar", flush=True)
                pos.tap_step("edit", "edit-resend")
                time.sleep(0.15)
                try:
                    ex.scrcpy.set_clipboard(VERSE_FALLBACK, paste=True)
                except Exception:
                    sh("shell", "input", "keyevent", "279")
                time.sleep(t["after_paste_s"])
                sh("shell", "input", "keyevent", "4")
                time.sleep(0.2)
                nav.refresh("f07-paste")
            nav.refresh("f07b")
            if not pos.tap_step("send", "send"):
                return False
            time.sleep(t["after_send_s"])
            nav.refresh("f15")
            blob = nav.labels()
            return status_published(nav) or (
                ("mes mises" in blob or "nouveau message de statut" in blob)
                and "ecrivez un statut" not in blob
                and "couleur de fond" not in blob
            )

        ok = do_send()
        if not ok:
            ok = pos.retry_from_last("send", do_send, max_tries=3, need_composer=True)

        try:
            save("f15-final", grab_norm(ex))
        except Exception:
            pass

        elapsed = round(time.time() - t0, 1)
        pos.persist(ok, elapsed)
        print(
            "FINAL_OK" if ok else "FINAL_CHECK",
            nav.labels()[:200],
            flush=True,
        )
        print(
            "DONE FAST elapsed_s=",
            elapsed,
            "ok=",
            ok,
            "color=",
            ok_color,
            "font=",
            ok_font,
            flush=True,
        )
    finally:
        ex.close()

    return ok, elapsed, {"color": ok_color, "font": ok_font}


def main_auto() -> None:
    """Autoaperfeiçoamento: gravar → sucesso → apertar timing → até velocidade razoável."""
    from vision_agent.auto_improve import run_auto_improve

    cache = load_step_cache()
    result = run_auto_improve(
        main_fast,
        adb=ADB,
        serial=SERIAL,
        cache=cache,
        cache_path=CACHE_PATH,
    )
    print("AUTO_RESULT", result, flush=True)


def main() -> None:
    print("YV_REMOTE_TEST", SERIAL, flush=True)
    ex = ActionExecutor(serial=SERIAL)
    assert ex.backend == "scrcpy", ex.scrcpy.last_error
    print("device", ex.device_w, ex.device_h, "backend", ex.backend, flush=True)

    nav = RemoteNavigator(
        ex,
        serial=SERIAL,
        adb_path=ADB,
        grab_fn=lambda: grab_norm(ex),
        save_fn=save,
    )

    try:
        # 1) YouVersion
        print("1 YouVersion deep link", flush=True)
        sh("shell", "am", "force-stop", "com.sirma.mobile.bible.android")
        time.sleep(0.5)
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
        time.sleep(4.0)
        focus = sh("shell", "dumpsys", "window").stdout.decode("utf-8", "replace")
        if "bible" not in focus.lower():
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
            time.sleep(3.0)
        nav.refresh("01-yv")

        # 2) long-press verso + Copy (aria)
        print("2 select + Copy (remote aria)", flush=True)
        verse = find_by_aria(
            nav.marks,
            "quant à toi",
            "4quant",
            "paroles cach",
            "daniel, tiens",
        )
        if verse:
            nav.tap(verse, why="verse-long", long=True, verify=False)
        else:
            ex.execute({"acao": "long_click", "coordenadas": {"x": 120, "y": 670}})
            time.sleep(0.8)
        time.sleep(1.2)
        nav.refresh("02-sel")
        (OUT / "02-sel.xml").write_text(
            dump_ui_xml(ADB, SERIAL) or nav.last_xml, encoding="utf-8"
        )

        copied = nav.go(
            "copy",
            "copier",
            "copiar",
            why="copy",
            max_tries=2,
        ).ok
        if not copied:
            # XML bruto — botão toolbar às vezes fora do filtro SoM
            xml = nav.last_xml or dump_ui_xml(ADB, SERIAL)
            for node in re.finditer(r"<node\b[^>]*>", xml or ""):
                tag = node.group(0)
                if not re.search(r'(content-desc|text)="[^"]*[Cc]opy[^"]*"', tag):
                    continue
                bm = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', tag)
                if not bm:
                    continue
                x1, y1, x2, y2 = map(int, bm.groups())
                cx = int(((x1 + x2) / 2) * 1080 / ex.device_w)
                cy = int(((y1 + y2) / 2) * 1920 / ex.device_h)
                print("TAP Copy XML", cx, cy, flush=True)
                ex.execute(
                    {"acao": "click", "coordenadas": {"x": cx, "y": cy}, "verify": False}
                )
                copied = True
                break
        if not copied:
            print("WARN Copy UI miss — clipboard forçado", flush=True)

        try:
            ex.scrcpy.set_clipboard(VERSE_FALLBACK, paste=False)
            print("clipboard verse set", flush=True)
        except Exception as exc:
            print("clipboard warn", exc, flush=True)
        time.sleep(0.25)

        # 3) WhatsApp Status (aria: Actus / mises à jour / Status)
        print("3 WhatsApp Status (remote)", flush=True)
        sh("shell", "am", "start", "-n", "com.whatsapp/.home.ui.HomeActivity")
        time.sleep(1.3)
        nav.refresh("04-home")

        # Já na aba status?
        on_status = nav.has(
            "nouveau message de statut",
            "mes mises à jour de statut",
            "nouvelle mise à jour de statut",
        )
        if not on_status:
            # Tab bar inferior: tenta aria; senão 2ª tab por posição (D-pad)
            went = nav.go(
                "actus",
                "statuts",
                "updates",
                "status",
                why="tab-status",
                expect=(
                    "nouveau message de statut",
                    "mes mises à jour",
                    "nouvelle mise à jour",
                ),
                max_tries=1,
            )
            if not went.ok:
                # Controlo remoto espacial: tabs no fundo, da esquerda
                # Discussions=1ª, Actus/Status=2ª (~405 canónico)
                bottom = sorted(
                    [m for m in nav.marks if m.cy > 1650],
                    key=lambda m: m.cx,
                )
                tab = None
                for m in bottom:
                    blob = (m.label + " " + m.aria).lower()
                    if any(
                        k in blob
                        for k in ("actus", "statut", "status", "update", "novedad")
                    ):
                        tab = m
                        break
                if tab is None and len(bottom) >= 2:
                    tab = bottom[1] if len(bottom) > 2 else bottom[min(1, len(bottom) - 1)]
                # WhatsApp FR: muitas vezes 4 tabs — Status é a 2ª (index 1)
                if tab is None:
                    ex.execute(
                        {
                            "acao": "click",
                            "coordenadas": {"x": 405, "y": 1840},
                            "verify": False,
                        }
                    )
                else:
                    nav.tap(tab, why="tab-status-spatial", verify=False)
                time.sleep(1.1)
                nav.refresh("04b-status")

        nav.refresh("05-actus")
        if not nav.has("nouveau message de statut", "message de statut"):
            # ainda em Discussions — tenta deep link Status + tab
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
            time.sleep(1.4)
            nav.refresh("05b-actus")
            if not nav.has("nouveau message de statut", "mes mises à jour"):
                ex.execute(
                    {
                        "acao": "click",
                        "coordenadas": {"x": 405, "y": 1840},
                        "verify": False,
                    }
                )
                time.sleep(1.2)
                nav.refresh("05c-actus")

        pencil = nav.go(
            "nouveau message de statut",
            "nouveau message de status",
            why="pencil",
            expect=("écrivez", "écrivez un statut", "type a status", "sans serif"),
            max_tries=2,
        )
        if not pencil.ok:
            fab = find_by_aria(
                nav.marks,
                "nouveau message de statut",
                "extended_mini_fab",
                min_score=350,
            )
            if fab:
                nav.tap(fab, why="pencil-fab", verify=False)
                time.sleep(1.0)
                nav.refresh("05c-composer")
            if not nav.has("écrivez", "écrivez un statut", "sans serif"):
                raise SystemExit("Botão status texto não encontrado (aria remote)")
        time.sleep(0.8)

        # 4) paste
        print("4 paste", flush=True)
        nav.refresh("06-composer")
        nav.go(
            "écrivez un statut",
            "écrivez",
            "edittext",
            "type a status",
            why="edit",
            max_tries=2,
        )
        try:
            ex.scrcpy.set_clipboard(VERSE_FALLBACK, paste=True)
        except Exception:
            sh("shell", "input", "keyevent", "279")
        time.sleep(1.0)
        nav.refresh("07-pasted")
        # fecha teclado se TERMINÉ/Done existir; senão BACK
        if not nav.go("terminé", "termine", "done", why="kb-done", max_tries=1).ok:
            sh("shell", "input", "keyevent", "4")
            time.sleep(0.4)
            nav.refresh("07b")
        nav.dismiss_if_needed()

        # 5) cor — 3ª a contar do canto direito da palette
        print("5 cor remote (3ª do canto direito)", flush=True)
        nav.refresh("08-precolor")
        nav.go(
            "couleur de fond",
            "color_picker",
            "palette",
            why="palette",
            expect=("terminé", "soleil", "jaune", "violine", "pêche", "wasabi", "émeraude"),
            absent=("abandonner le texte",),
            max_tries=2,
        )
        time.sleep(0.5)
        nav.refresh("09-colors")
        # Se abriu tipografia por engano, fecha e reabre cor
        row_blob = " ".join(
            m.label.lower() for m in nav.marks if 1400 < m.cy < 1600
        )
        if any(k in row_blob for k in ("serif", "courier", "calistoga", "morning")) and not any(
            k in row_blob for k in ("soleil", "violine", "pêche", "wasabi", "émeraude")
        ):
            print("WARN painel de fontes em vez de cores — corrigindo", flush=True)
            nav.go("terminé", "termine", "done", why="close-wrong-panel", max_tries=1)
            time.sleep(0.3)
            nav.refresh("09-fix-palette")
            nav.go(
                "couleur de fond",
                why="palette-retry",
                expect=("soleil", "jaune", "violine", "pêche", "terminé"),
                max_tries=2,
            )
            nav.refresh("09-colors")

        pick_color_from_right(nav, index_from_right=COLOR_FROM_RIGHT)
        nav.refresh("10-color-locked")
        nav.dismiss_if_needed()
        nav.refresh("10b")
        nav.go("terminé", "termine", "done", why="termine-color", max_tries=2)
        time.sleep(0.25)

        # 6) tipografia
        print("6 fonte remote", flush=True)
        nav.refresh("11-prefont")
        nav.dismiss_if_needed()
        aa = nav.go(
            "sans serif",
            "font_picker",
            why="aa",
            expect=("terminé", "bold", "serif", "sans"),
            absent=("abandonner",),
            max_tries=2,
        )
        if not aa.ok:
            ex.execute(
                {"acao": "click", "coordenadas": {"x": 834, "y": 160}, "verify": False}
            )
            time.sleep(0.7)
            nav.refresh("11b-fonts")
        nav.dismiss_if_needed()
        nav.refresh("12-fonts")
        font = nav.swipe_until(
            "sans bold",
            "bold",
            row_cy_min=1300,
            row_cy_max=1650,
            max_swipes=4,
            why="font",
        )
        if not font.ok:
            # último da fila como fallback tipográfico
            row = [m for m in nav.marks if 1300 < m.cy < 1650 and m.area < 80_000]
            row.sort(key=lambda m: m.cx)
            if row:
                nav.tap(row[-1], why="font-fallback", verify=False)
        time.sleep(0.35)
        nav.refresh("13-styled")
        nav.dismiss_if_needed()
        nav.go("terminé", "termine", "done", why="termine-font", max_tries=2)
        time.sleep(0.3)

        # 7) Envoyer
        print("7 Envoyer (remote)", flush=True)
        nav.refresh("14-ready")
        nav.dismiss_if_needed()
        if nav.has("350", "ligne"):
            nav.go("ok", "d'accord", why="limit-ok", max_tries=1)
            time.sleep(0.3)
            nav.refresh("14b")
        sent = nav.go(
            "envoyer",
            "send",
            why="send",
            expect=(
                "vu par",
                "mes mises à jour de statut",
                "nouveau message de statut",
                "discussions",
                "instantané",
                "partagé",
            ),
            absent=("écrivez un statut", "couleur de fond"),
            max_tries=2,
        )
        time.sleep(1.2)
        nav.refresh("15-final")
        ok = status_published(nav) or (
            not nav.has("écrivez un statut", "envoyer")
            and nav.has("discussions", "actus", "mes mises à jour", "statuts")
        )
        if not ok and nav.has("envoyer"):
            nav.go("envoyer", "send", why="send-retry", max_tries=1)
            time.sleep(1.5)
            nav.refresh("15b-final")
            ok = status_published(nav)
        print(
            "FINAL_OK" if ok else "FINAL_CHECK",
            nav.labels()[:240],
            flush=True,
        )
        print("DONE out=", OUT, "sent=", sent.ok, flush=True)
    finally:
        ex.close()


if __name__ == "__main__":
    # --auto: gravar + loop de aperfeiçoamento. --full: exploração. default: rápido.
    args = [a.lower() for a in sys.argv[1:]]
    if "--auto" in args or os.environ.get("VISION_YV_AUTO") == "1":
        main_auto()
    elif "--full" in args or os.environ.get("VISION_YV_FULL") == "1":
        main()
    else:
        main_fast()
