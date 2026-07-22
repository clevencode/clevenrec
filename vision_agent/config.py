"""Configuração do vision_agent — captura ADB/scrcpy + SoM (sem IA)."""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
FRAMES_DIR = PACKAGE_DIR / "frames"

# Resolução canônica para coordenadas / SoM
CANONICAL_WIDTH = 1080
CANONICAL_HEIGHT = 1920

# Filtro de estabilidade (loop de captura)
CHANGE_THRESHOLD = 0.02
DIFF_PIXEL_THRESHOLD = 25
JPEG_QUALITY = 70
CAPTURE_INTERVAL_S = 0.25

ADB_SERIAL = os.environ.get("VISION_ADB_SERIAL") or None

_DEFAULT_ADB_CANDIDATES = [
    os.environ.get("ADB_PATH") or "",
    "adb",
    "adb.exe",
]

_DEFAULT_SCRCPY_DIR_CANDIDATES = [
    os.environ.get("SCRCPY_DIR") or "",
    os.environ.get("SCRCPY_PATH") or "",
]


def resolve_adb_path() -> str:
    """Retorna adb.exe existente ou 'adb' no PATH."""
    for candidate in _DEFAULT_ADB_CANDIDATES:
        if not candidate:
            continue
        p = Path(candidate)
        if p.is_file():
            return str(p)
        if candidate in ("adb", "adb.exe") and os.sep not in candidate and "/" not in candidate:
            return candidate
    return "adb"


def resolve_scrcpy_dir() -> Path | None:
    """Pasta com scrcpy.exe / scrcpy-server (env ou WinGet)."""
    for candidate in _DEFAULT_SCRCPY_DIR_CANDIDATES:
        if not candidate:
            continue
        p = Path(candidate)
        if p.is_file():
            p = p.parent
        if not p.is_dir():
            continue
        if (p / "scrcpy-server").is_file() or (p / "scrcpy-server.jar").is_file():
            return p
        if (p / "scrcpy.exe").is_file():
            return p
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if winget.is_dir():
        for exe in winget.rglob("scrcpy.exe"):
            return exe.parent
    return None


# Gestos (scrcpy / ADB)
TAP_DOWN_UP_S = float(os.environ.get("VISION_TAP_DOWN_UP", "0.02"))
SCRCPY_SWIPE_MS = int(os.environ.get("VISION_SCRCPY_SWIPE_MS", "280"))
SCRCPY_SWIPE_STEPS = int(os.environ.get("VISION_SCRCPY_SWIPE_STEPS", "12"))
ADB_SWIPE_MS = int(os.environ.get("VISION_ADB_SWIPE_MS", "300"))
LONG_PRESS_MS = int(os.environ.get("VISION_LONG_PRESS_MS", "600"))

# Stream scrcpy
SCRCPY_MAX_SIZE = int(os.environ.get("VISION_SCRCPY_MAX_SIZE", "0"))
SCRCPY_VIDEO_BIT_RATE = int(os.environ.get("VISION_SCRCPY_BITRATE", "8000000"))
SCRCPY_MAX_FPS = int(os.environ.get("VISION_SCRCPY_MAX_FPS", "60"))
CAPTURE_BACKEND = (os.environ.get("VISION_CAPTURE_BACKEND") or "auto").lower()

# Set-of-Mark
SOM_MAX_MARKS = int(os.environ.get("VISION_SOM_MAX_MARKS", "36"))
SOM_MIN_SIDE = int(os.environ.get("VISION_SOM_MIN_SIDE", "24"))

# Precisão de navegabilidade (estilo feedback de carro autônomo)
# Inset relativo ao bounds — evita bordas mortas do Android
HIT_INSET_FRAC = float(os.environ.get("VISION_HIT_INSET_FRAC", "0.12"))
HIT_INSET_MIN_PX = int(os.environ.get("VISION_HIT_INSET_MIN", "4"))
HIT_INSET_MAX_PX = int(os.environ.get("VISION_HIT_INSET_MAX", "22"))
# Snap de coords livres → marca SoM (px canônicos)
SNAP_MAX_DIST_PX = float(os.environ.get("VISION_SNAP_MAX_DIST", "80"))
# Verificação pós-toque + retry com micro-offsets
PRECISION_VERIFY = (os.environ.get("VISION_PRECISION_VERIFY") or "1") not in (
    "0",
    "false",
    "no",
)
PRECISION_VERIFY_THRESHOLD = float(
    os.environ.get("VISION_PRECISION_VERIFY_THRESHOLD", "0.004")
)
PRECISION_MAX_RETRIES = int(os.environ.get("VISION_PRECISION_MAX_RETRIES", "4"))
PRECISION_OFFSET_STEP_PX = int(os.environ.get("VISION_PRECISION_OFFSET_STEP", "14"))

# Settle pós-ação (aguardar UI estabilizar)
_raw_step_delay = os.environ.get("VISION_AGENT_STEP_DELAY")
if _raw_step_delay is None:
    SETTLE_SCALE = 1.0
else:
    _parsed = float(_raw_step_delay)
    SETTLE_SCALE = 1.0 if abs(_parsed - 0.45) < 1e-6 else max(0.1, _parsed)

SETTLE_CLICK_S = 0.22 * SETTLE_SCALE
SETTLE_SWIPE_S = 0.40 * SETTLE_SCALE
SETTLE_TEXT_S = 0.35 * SETTLE_SCALE
SETTLE_IDLE_S = 0.20 * SETTLE_SCALE
AGENT_STEP_DELAY_S = SETTLE_CLICK_S


def settle_for_action(acao: str | None) -> float:
    """Tempo de settle pós-ação alinhado ao tipo de gesto."""
    a = (acao or "").strip().lower()
    if a in ("click", "long_click"):
        return SETTLE_CLICK_S
    if a in ("swipe_up", "swipe_down"):
        return SETTLE_SWIPE_S
    if a == "write_text":
        return SETTLE_TEXT_S
    if a == "aguardar":
        return SETTLE_IDLE_S
    return SETTLE_CLICK_S
