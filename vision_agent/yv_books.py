"""Navegação por livros na YouVersion Bible (FR/S21).

Dois caminhos (tab-first / atalho):
  1) DEEPLINK (mais rápido): bible.com/bible/{vid}/{BOOK}.{CH}.{V}.{abbr}
  2) UI: chip inferior \"Genèse 1\" → lista Books → livro → grelha de capítulos

Aprendido em teach/explore 2026-07-21 (device 720×1600).
"""

from __future__ import annotations

import re
import time
from typing import Optional

from vision_agent.remote import RemoteNavigator, find_by_aria
from vision_agent.precision import map_canonical_to_physical

# USFM / YouVersion path codes → rótulos UI FR (S21) e aliases
BOOK_CATALOG: dict[str, dict] = {
    "GEN": {"fr": ["Genèse", "Genesis"], "en": ["Genesis"]},
    "EXO": {"fr": ["Exode", "Exodus"], "en": ["Exodus"]},
    "LEV": {"fr": ["Lévitique", "Leviticus"], "en": ["Leviticus"]},
    "NUM": {"fr": ["Nombres", "Numbers"], "en": ["Numbers"]},
    "DEU": {"fr": ["Deutéronome", "Deuteronomy"], "en": ["Deuteronomy"]},
    "JOS": {"fr": ["Josué", "Joshua"], "en": ["Joshua"]},
    "JDG": {"fr": ["Juges", "Judges"], "en": ["Judges"]},
    "RUT": {"fr": ["Ruth"], "en": ["Ruth"]},
    "1SA": {"fr": ["1 Samuel"], "en": ["1 Samuel"]},
    "2SA": {"fr": ["2 Samuel"], "en": ["2 Samuel"]},
    "1KI": {"fr": ["1 Rois", "1 Kings"], "en": ["1 Kings"]},
    "2KI": {"fr": ["2 Rois", "2 Kings"], "en": ["2 Kings"]},
    "1CH": {"fr": ["1 Chroniques", "1 Chronicles"], "en": ["1 Chronicles"]},
    "2CH": {"fr": ["2 Chroniques", "2 Chronicles"], "en": ["2 Chronicles"]},
    "EZR": {"fr": ["Esdras", "Ezra"], "en": ["Ezra"]},
    "NEH": {"fr": ["Néhémie", "Nehemiah"], "en": ["Nehemiah"]},
    "EST": {"fr": ["Esther"], "en": ["Esther"]},
    "JOB": {"fr": ["Job"], "en": ["Job"]},
    "PSA": {"fr": ["Psaumes", "Psaume", "Psalms", "Psalm"], "en": ["Psalms"]},
    "PRO": {"fr": ["Proverbes", "Proverbs"], "en": ["Proverbs"]},
    "ECC": {"fr": ["Ecclésiaste", "Ecclesiastes"], "en": ["Ecclesiastes"]},
    "SNG": {"fr": ["Cantique", "Song"], "en": ["Song of Solomon"]},
    "ISA": {"fr": ["Ésaïe", "Esaie", "Isaiah"], "en": ["Isaiah"]},
    "JER": {"fr": ["Jérémie", "Jeremiah"], "en": ["Jeremiah"]},
    "LAM": {"fr": ["Lamentations"], "en": ["Lamentations"]},
    "EZK": {"fr": ["Ézéchiel", "Ezekiel"], "en": ["Ezekiel"]},
    "DAN": {"fr": ["Daniel"], "en": ["Daniel"]},
    "HOS": {"fr": ["Osée", "Hosea"], "en": ["Hosea"]},
    "JOL": {"fr": ["Joël", "Joel"], "en": ["Joel"]},
    "AMO": {"fr": ["Amos"], "en": ["Amos"]},
    "OBA": {"fr": ["Abdias", "Obadiah"], "en": ["Obadiah"]},
    "JON": {"fr": ["Jonas", "Jonah"], "en": ["Jonah"]},
    "MIC": {"fr": ["Michée", "Micah"], "en": ["Micah"]},
    "NAM": {"fr": ["Nahum"], "en": ["Nahum"]},
    "HAB": {"fr": ["Habakuk", "Habakkuk"], "en": ["Habakkuk"]},
    "ZEP": {"fr": ["Sophonie", "Zephaniah"], "en": ["Zephaniah"]},
    "HAG": {"fr": ["Aggée", "Haggai"], "en": ["Haggai"]},
    "ZEC": {"fr": ["Zacharie", "Zechariah"], "en": ["Zechariah"]},
    "MAL": {"fr": ["Malachie", "Malachi"], "en": ["Malachi"]},
    "MAT": {"fr": ["Matthieu", "Matthew"], "en": ["Matthew"]},
    "MRK": {"fr": ["Marc", "Mark"], "en": ["Mark"]},
    "LUK": {"fr": ["Luc", "Luke"], "en": ["Luke"]},
    "JHN": {"fr": ["Jean", "John"], "en": ["John"]},
    "ACT": {"fr": ["Actes", "Acts"], "en": ["Acts"]},
    "ROM": {"fr": ["Romains", "Romans"], "en": ["Romans"]},
    "1CO": {"fr": ["1 Corinthiens", "1 Corinthians"], "en": ["1 Corinthians"]},
    "2CO": {"fr": ["2 Corinthiens", "2 Corinthians"], "en": ["2 Corinthians"]},
    "GAL": {"fr": ["Galates", "Galatians"], "en": ["Galatians"]},
    "EPH": {"fr": ["Éphésiens", "Ephesians"], "en": ["Ephesians"]},
    "PHP": {"fr": ["Philippiens", "Philippians"], "en": ["Philippians"]},
    "COL": {"fr": ["Colossiens", "Colossians"], "en": ["Colossians"]},
    "1TH": {"fr": ["1 Thessaloniciens", "1 Thessalonians"], "en": ["1 Thessalonians"]},
    "2TH": {"fr": ["2 Thessaloniciens", "2 Thessalonians"], "en": ["2 Thessalonians"]},
    "1TI": {"fr": ["1 Timothée", "1 Timothy"], "en": ["1 Timothy"]},
    "2TI": {"fr": ["2 Timothée", "2 Timothy"], "en": ["2 Timothy"]},
    "TIT": {"fr": ["Tite", "Titus"], "en": ["Titus"]},
    "PHM": {"fr": ["Philémon", "Philemon"], "en": ["Philemon"]},
    "HEB": {"fr": ["Hébreux", "Hebrews"], "en": ["Hebrews"]},
    "JAS": {"fr": ["Jacques", "James"], "en": ["James"]},
    "1PE": {"fr": ["1 Pierre", "1 Peter"], "en": ["1 Peter"]},
    "2PE": {"fr": ["2 Pierre", "2 Peter"], "en": ["2 Peter"]},
    "1JN": {"fr": ["1 Jean", "1 John"], "en": ["1 John"]},
    "2JN": {"fr": ["2 Jean", "2 John"], "en": ["2 John"]},
    "3JN": {"fr": ["3 Jean", "3 John"], "en": ["3 John"]},
    "JUD": {"fr": ["Jude"], "en": ["Jude"]},
    "REV": {"fr": ["Apocalypse", "Revelation"], "en": ["Revelation"]},
}

DEFAULT_VERSION_ID = 152  # S21
DEFAULT_ABBR = "S21"


def deeplink(book: str, chapter: int = 1, verse: int = 1, *, version_id: int = DEFAULT_VERSION_ID, abbr: str = DEFAULT_ABBR) -> str:
    code = book.upper().strip()
    return f"https://www.bible.com/bible/{version_id}/{code}.{int(chapter)}.{int(verse)}.{abbr}"


def book_aria(book: str) -> tuple[str, ...]:
    code = book.upper().strip()
    meta = BOOK_CATALOG.get(code) or {}
    names = list(meta.get("fr") or []) + list(meta.get("en") or [])
    if not names:
        names = [code]
    # needles curtos para find_by_aria
    out: list[str] = []
    for n in names:
        out.append(n)
        out.append(n.lower())
    return tuple(dict.fromkeys(out))


def open_reader_deeplink(sh, book: str, chapter: int = 1, verse: int = 1) -> str:
    url = deeplink(book, chapter, verse)
    sh(
        "shell",
        "am",
        "start",
        "-a",
        "android.intent.action.VIEW",
        "-d",
        url,
        "com.sirma.mobile.bible.android",
    )
    return url


def _swipe_list(nav: RemoteNavigator, *, up: bool = True) -> None:
    ex = nav.ex
    y1, y2 = (1400, 600) if up else (600, 1400)
    px1, py1 = map_canonical_to_physical(540, y1, ex.device_w, ex.device_h)
    px2, py2 = map_canonical_to_physical(540, y2, ex.device_w, ex.device_h)
    try:
        if ex.backend == "scrcpy" and ex.scrcpy.connected:
            ex.scrcpy.swipe(px1, py1, px2, py2, duration_ms=280)
        else:
            ex.adb.swipe(px1, py1, px2, py2, duration_ms=280)
    except Exception as exc:
        print("YV_BOOKS swipe err", exc, flush=True)


def open_book_picker(nav: RemoteNavigator) -> bool:
    """Tab: chip inferior \"Genèse 1\" / livro actual (cy>1650)."""
    nav.refresh("yv-picker")
    chip = None
    for m in nav.marks:
        lab = (m.label or "").strip()
        if m.cy < 1650:
            continue
        # \"Genèse 1\", \"Exode 3\", \"Daniel 12\"
        if re.search(r".+\s+\d+$", lab) or any(
            k in lab.lower() for k in ("genèse", "genese", "exode", "daniel", "jean", "psaume")
        ):
            chip = m
            break
    if not chip:
        # fallback coords aprendidas
        print("YV_BOOKS chip fallback xy", flush=True)
        nav.ex.execute(
            {"acao": "click", "coordenadas": {"x": 636, "y": 1759}, "verify": False}
        )
    else:
        nav.tap(chip, why="book-chip", verify=False)
    time.sleep(0.9)
    nav.refresh("yv-picker-open")
    return nav.has("books", "genèse", "exode", "history", "edittext")


def _search_query(book: str) -> str:
    """Query ASCII-safe para EditText (ADB input text não gosta de acentos)."""
    code = book.upper().strip()
    meta = BOOK_CATALOG.get(code) or {}
    names = list(meta.get("fr") or []) + list(meta.get("en") or [])
    raw = names[0] if names else code
    # strip accents common in FR bible names
    table = str.maketrans(
        {
            "é": "e",
            "è": "e",
            "ê": "e",
            "ë": "e",
            "à": "a",
            "â": "a",
            "ù": "u",
            "û": "u",
            "ô": "o",
            "î": "i",
            "ï": "i",
            "ç": "c",
            "É": "E",
            "È": "E",
            "Ê": "E",
            "À": "A",
            "Â": "A",
            "Ù": "U",
            "Ô": "O",
            "Î": "I",
            "Ç": "C",
        }
    )
    return raw.translate(table)


def _tap_book_if_visible(nav: RemoteNavigator, book: str, aria: tuple[str, ...]) -> bool:
    primary = (BOOK_CATALOG.get(book.upper(), {}).get("fr") or [book])[0]
    # prefer exact label (case-insensitive)
    exact = None
    for m in nav.marks:
        if 250 < m.cy < 1750 and (m.label or "").strip().lower() == primary.lower():
            exact = m
            break
    m = exact or find_by_aria(nav.marks, *aria)
    if m and 250 < m.cy < 1750:
        nav.tap(m, why=f"book-{book}", verify=False)
        time.sleep(0.8)
        nav.refresh("yv-after-book")
        return True
    return False


def pick_book_in_list(nav: RemoteNavigator, book: str, *, max_scrolls: int = 16) -> bool:
    """Lista Books: search EditText primeiro; scroll bidirecional se preciso."""
    aria = book_aria(book)
    nav.refresh("yv-books")
    if not nav.has("books", "genèse", "exode", "daniel", "history", "edittext"):
        if not open_book_picker(nav):
            return False
    tab = find_by_aria(nav.marks, "books", "livres")
    if tab:
        nav.tap(tab, why="books-tab", verify=False)
        time.sleep(0.35)
        nav.refresh("yv-books-tab")

    if _tap_book_if_visible(nav, book, aria):
        return True

    # Atalho: filtrar pela caixa de pesquisa (evita scroll infinito a partir do meio)
    edit = find_by_aria(nav.marks, "edittext")
    if edit:
        q = _search_query(book)
        print(f"YV_BOOKS search '{q}'", flush=True)
        nav.tap(edit, why="book-search", verify=False)
        time.sleep(0.35)
        try:
            nav.ex.execute({"acao": "write_text", "texto_input": q, "verify": False})
        except Exception as exc:
            print("YV_BOOKS search type err", exc, flush=True)
        time.sleep(0.7)
        nav.refresh("yv-book-search")
        if _tap_book_if_visible(nav, book, aria):
            return True
        # limpar filtro (DEL) e continuar com scroll
        try:
            for _ in range(len(q) + 2):
                nav.ex.adb.shell.run("input keyevent 67")  # DEL
        except Exception:
            try:
                nav.ex.execute({"acao": "write_text", "texto_input": " ", "verify": False})
            except Exception:
                pass
        time.sleep(0.3)
        nav.refresh("yv-book-search-clear")

    # Scroll bidirecional: primeiro para cima (livros anteriores), depois para baixo
    for direction_up, label in ((False, "earlier"), (True, "later")):
        for i in range(max_scrolls // 2 + 1):
            if _tap_book_if_visible(nav, book, aria):
                return True
            print(f"YV_BOOKS scroll {label} book={book} i={i}", flush=True)
            _swipe_list(nav, up=direction_up)
            time.sleep(0.35)
            nav.refresh(f"yv-book-{label}-{i}")
            nav.dismiss_if_needed()
    return False


def pick_chapter(nav: RemoteNavigator, chapter: int) -> bool:
    """Grelha de capítulos sob o livro expandido — tab no número."""
    want = str(int(chapter))
    nav.refresh("yv-ch")
    nums = [
        m
        for m in nav.marks
        if re.fullmatch(r"\d{1,3}", (m.label or "").strip()) and m.cy > 400
    ]
    for m in nums:
        if m.label.strip() == want:
            nav.tap(m, why=f"chapter-{want}", verify=False)
            time.sleep(0.9)
            nav.refresh("yv-after-ch")
            return True
    # scroll grelha se capítulo alto
    for i in range(6):
        _swipe_list(nav, up=True)
        time.sleep(0.3)
        nav.refresh(f"yv-ch-s{i}")
        for m in nav.marks:
            if (m.label or "").strip() == want and re.fullmatch(r"\d{1,3}", m.label.strip()):
                nav.tap(m, why=f"chapter-{want}", verify=False)
                time.sleep(0.9)
                nav.refresh("yv-after-ch")
                return True
    return False


def go_to_book_ui(
    nav: RemoteNavigator,
    book: str,
    chapter: int = 1,
) -> bool:
    """Caminho UI completo a partir do reader."""
    if not open_book_picker(nav):
        return False
    if not pick_book_in_list(nav, book):
        return False
    if not pick_chapter(nav, chapter):
        return False
    # confirmar reader (chip inferior ou título)
    blob = nav.labels()
    names = [a.lower() for a in book_aria(book)]
    ok = any(n[:4] in blob for n in names if len(n) >= 4)
    print(f"YV_BOOKS UI ok={ok} book={book} ch={chapter}", flush=True)
    return ok


def go_to_book(
    nav: RemoteNavigator,
    sh,
    book: str,
    chapter: int = 1,
    verse: int = 1,
    *,
    prefer_deeplink: bool = True,
) -> bool:
    """API canónica: deeplink primeiro; UI se prefer_deeplink=False ou falhar verificação."""
    code = book.upper().strip()
    if prefer_deeplink:
        url = open_reader_deeplink(sh, code, chapter, verse)
        print(f"YV_BOOKS deeplink {url}", flush=True)
        time.sleep(1.8)
        nav.refresh("yv-dl")
        names = [a.lower() for a in book_aria(code)]
        blob = nav.labels()
        if any(n[:4] in blob for n in names if len(n) >= 4) or nav.has("navigate back", "s21"):
            # se deeplink abriu reader genérico, ok se não estamos no home feed
            if not nav.has("verse of the day", "good evening", "tab 1 of 5"):
                print("YV_BOOKS deeplink OK", flush=True)
                return True
        print("YV_BOOKS deeplink fraco → UI", flush=True)
    return go_to_book_ui(nav, code, chapter)


def _cli_main() -> None:
    """Validação rápida: deeplink DAN/JHN + UI Exode."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(ROOT))

    from vision_agent.config import resolve_adb_path
    from vision_agent.executor import ActionExecutor
    from vision_agent.normalize import normalize_frame

    def _serial() -> str:
        env = os.environ.get("VISION_ADB_SERIAL")
        if env:
            return env
        adb = resolve_adb_path()
        out = subprocess.run([adb, "devices"], capture_output=True, timeout=8).stdout.decode()
        for line in out.splitlines()[1:]:
            if "\tdevice" in line:
                return line.split("\t", 1)[0].strip()
        raise SystemExit("sem device ADB")

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

    results: list[tuple[str, bool]] = []

    # 1) deeplink Daniel 12
    ok1 = go_to_book(nav, sh, "DAN", 12, 1, prefer_deeplink=True)
    results.append(("deeplink DAN.12", ok1))

    # 2) UI: Genèse → Exode 1 (a partir do reader actual)
    ok2 = go_to_book(nav, sh, "EXO", 1, 1, prefer_deeplink=False)
    results.append(("UI EXO.1", ok2))

    # 3) deeplink Jean 3
    ok3 = go_to_book(nav, sh, "JHN", 3, 16, prefer_deeplink=True)
    results.append(("deeplink JHN.3.16", ok3))

    for name, ok in results:
        print(f"RESULT {name}: {'OK' if ok else 'FAIL'}", flush=True)
    if not all(ok for _, ok in results):
        raise SystemExit(1)
    print("YV_BOOKS ALL_OK", flush=True)


if __name__ == "__main__":
    _cli_main()
