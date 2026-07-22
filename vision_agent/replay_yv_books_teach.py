"""Replay dos passos teach (events_20260721_230239):
  Books list → scroll NT → profetas → 1 Thessaloniciens → cap. 1 → reader
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vision_agent.config import resolve_adb_path
from vision_agent.executor import ActionExecutor
from vision_agent.normalize import normalize_frame
from vision_agent.precision import map_canonical_to_physical
from vision_agent.remote import RemoteNavigator, find_by_aria
from vision_agent.yv_books import (
    open_book_picker,
    open_reader_deeplink,
    pick_chapter,
    _swipe_list,
    _tap_book_if_visible,
    book_aria,
)


def _serial() -> str:
    env = os.environ.get("VISION_ADB_SERIAL")
    if env:
        return env
    adb = resolve_adb_path()
    out = subprocess.run([adb, "devices"], capture_output=True, timeout=8).stdout.decode()
    for line in out.splitlines()[1:]:
        if "\tdevice" in line:
            return line.split("\t", 1)[0].strip()
    return "192.168.1.161:5555"


def main() -> None:
    serial = _serial()
    adb = resolve_adb_path()

    def sh(*a, timeout=40):
        return subprocess.run([adb, "-s", serial, *a], capture_output=True, timeout=timeout)

    ex = ActionExecutor(serial=serial)
    assert ex.backend == "scrcpy", getattr(ex.scrcpy, "last_error", None)

    def grab():
        import cv2
        import numpy as np

        if ex.backend == "scrcpy" and ex.scrcpy.connected:
            fr = ex.scrcpy.wait_frame(2.0)
            if fr is not None:
                return normalize_frame(fr)
        raw = subprocess.run(
            [adb, "-s", serial, "exec-out", "screencap", "-p"],
            capture_output=True,
            timeout=20,
        ).stdout
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("screencap falhou")
        return normalize_frame(img)

    nav = RemoteNavigator(
        ex, serial=serial, adb_path=adb, grab_fn=grab, log=lambda s: print(s, flush=True)
    )

    def blob() -> str:
        return nav.labels()

    def has_any(*needles: str) -> bool:
        b = blob()
        return any(n.lower() in b for n in needles)

    print("REPLAY start — espelhar teach 23:02", flush=True)

    # 0) garantir reader (deeplink Jean como ponto de partida próximo do teach)
    open_reader_deeplink(sh, "JHN", 1, 1)
    time.sleep(1.6)
    nav.refresh("r0-reader")
    if nav.has("books", "history", "edittext") and not nav.has("navigate back") is False:
        # já no picker
        pass
    elif not open_book_picker(nav):
        # chip fallback
        print("REPLAY force chip", flush=True)
        ex.execute({"acao": "click", "coordenadas": {"x": 636, "y": 1759}, "verify": False})
        time.sleep(0.9)
        nav.refresh("r0b-picker")

    if not nav.has("books", "history"):
        raise SystemExit("REPLAY FAIL: picker Books não abriu")

    tab = find_by_aria(nav.marks, "books", "livres")
    if tab:
        nav.tap(tab, why="books-tab", verify=False)
        time.sleep(0.3)
        nav.refresh("r1-books")

    # 1) Teach: zona NT mid (Jean, Actes, Romains…)
    print("REPLAY step1 → zona Jean/Actes", flush=True)
    for i in range(18):
        if has_any("jean", "actes", "romains"):
            print(f"REPLAY step1 OK i={i}", flush=True)
            break
        # se estamos no fim (Apocalypse), scroll earlier; senão later
        if has_any("apocalypse", "jude", "tite"):
            _swipe_list(nav, up=False)
        elif has_any("genèse", "exode", "ésaïe", "esaie", "daniel"):
            _swipe_list(nav, up=True)
        else:
            _swipe_list(nav, up=True)
        time.sleep(0.3)
        nav.refresh(f"r1-s{i}")
    else:
        print("REPLAY step1 WARN: Jean não visto", flush=True)

    # 2) Teach: scroll → profetas (Esaïe … Habakuk)
    print("REPLAY step2 → Esaïe/Daniel", flush=True)
    for i in range(20):
        if has_any("ésaïe", "esaie", "jérémie", "jeremie", "daniel", "ézéchiel", "ezechiel"):
            print(f"REPLAY step2 OK i={i}", flush=True)
            break
        # Jean→ atrás = earlier books = swipe down (up=False)
        _swipe_list(nav, up=False)
        time.sleep(0.3)
        nav.refresh(f"r2-s{i}")
    else:
        print("REPLAY step2 WARN: profetas não vistos", flush=True)

    # 3) Teach: 1 Thessaloniciens + grelha 1|2|3
    print("REPLAY step3 → 1 Thessaloniciens", flush=True)
    aria = book_aria("1TH")
    found = False
    for i in range(22):
        if _tap_book_if_visible(nav, "1TH", aria):
            found = True
            break
        # de Daniel/profetas para NT: scroll later
        _swipe_list(nav, up=True)
        time.sleep(0.3)
        nav.refresh(f"r3-s{i}")
    if not found:
        # search fallback (atalho)
        edit = find_by_aria(nav.marks, "edittext")
        if edit:
            nav.tap(edit, why="search-1th", verify=False)
            time.sleep(0.3)
            ex.execute({"acao": "write_text", "texto_input": "1 Thessaloniciens", "verify": False})
            time.sleep(0.8)
            nav.refresh("r3-search")
            found = _tap_book_if_visible(nav, "1TH", aria)
    if not found:
        raise SystemExit("REPLAY FAIL: 1 Thessaloniciens não encontrado")

    # 4) capítulo 1 (como no teach: números 1|2|3 inline)
    print("REPLAY step4 → capítulo 1", flush=True)
    time.sleep(0.4)
    nav.refresh("r4-ch")
    if not pick_chapter(nav, 1):
        # tap primeiro dígito visível
        nums = [
            m
            for m in nav.marks
            if re.fullmatch(r"\d{1,3}", (m.label or "").strip()) and m.cy > 350
        ]
        if not nums:
            raise SystemExit("REPLAY FAIL: grelha de capítulos vazia")
        nav.tap(sorted(nums, key=lambda m: (m.cy, m.cx))[0], why="ch-first", verify=False)
        time.sleep(1.0)
        nav.refresh("r4b")

    # 5) confirmar reader
    nav.refresh("r5-reader")
    ok = has_any("thessaloniciens", "1 thessaloniciens") or (
        nav.has("navigate back", "s21", "audio controls")
        and not nav.has("books", "history", "edittext")
    )
    # chip inferior
    for m in nav.marks:
        lab = (m.label or "").lower()
        if m.cy > 1650 and "thessalon" in lab:
            ok = True
    print(f"REPLAY done ok={ok} blob={blob()[:180]}", flush=True)
    if not ok:
        raise SystemExit(1)
    print("REPLAY ALL_OK", flush=True)


if __name__ == "__main__":
    main()
