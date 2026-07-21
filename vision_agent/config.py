"""Configuração do vision_agent (Bloco 1)."""

from __future__ import annotations

import os
from pathlib import Path

# Raiz do pacote e pasta de frames aprovados
PACKAGE_DIR = Path(__file__).resolve().parent
FRAMES_DIR = PACKAGE_DIR / "frames"

# Resolução canônica (coordenadas futuras padronizadas)
CANONICAL_WIDTH = 1080
CANONICAL_HEIGHT = 1920

# Filtro: fração mínima de pixels “diferentes” para aprovar o frame
CHANGE_THRESHOLD = 0.02
# Intensidade mínima (0–255) para considerar um pixel diferente
DIFF_PIXEL_THRESHOLD = 25

# JPEG de saída
JPEG_QUALITY = 70

# Intervalo entre capturas (segundos) — evita saturar ADB no MVP
CAPTURE_INTERVAL_S = 0.25

# ADB: serial opcional (None = primeiro device online)
ADB_SERIAL = os.environ.get("VISION_ADB_SERIAL") or None

# Candidatos comuns no Windows (scrcpy winget + PATH)
_DEFAULT_ADB_CANDIDATES = [
    os.environ.get("ADB_PATH") or "",
    r"C:\Users\Clevy\AppData\Local\Microsoft\WinGet\Packages\Genymobile.scrcpy_Microsoft.Winget.Source_8wekyb3d8bbwe\scrcpy-win64-v4.0\adb.exe",
    r"C:\Users\Clevy\AppData\Local\Microsoft\WinGet\Links\adb.exe",
    "adb",
    "adb.exe",
]

_DEFAULT_SCRCPY_DIR_CANDIDATES = [
    os.environ.get("SCRCPY_DIR") or "",
    os.environ.get("SCRCPY_PATH") or "",
    r"C:\Users\Clevy\AppData\Local\Microsoft\WinGet\Packages\Genymobile.scrcpy_Microsoft.Winget.Source_8wekyb3d8bbwe\scrcpy-win64-v4.0",
    r"C:\Users\Clevy\AppData\Local\Microsoft\WinGet\Links",
]


def resolve_adb_path() -> str:
    """Retorna o primeiro adb.exe existente ou 'adb' no PATH."""
    for candidate in _DEFAULT_ADB_CANDIDATES:
        if not candidate:
            continue
        p = Path(candidate)
        if p.is_file():
            return str(p)
        # nome simples — deixa o PATH resolver na execução
        if candidate in ("adb", "adb.exe") and os.sep not in candidate and "/" not in candidate:
            return candidate
    return "adb"


def resolve_scrcpy_dir() -> Path | None:
    """Pasta que contém scrcpy.exe e scrcpy-server."""
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
    # busca rápida em WinGet Packages
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if winget.is_dir():
        for exe in winget.rglob("scrcpy.exe"):
            return exe.parent
    return None


# Servidor do agente
AGENT_HOST = os.environ.get("VISION_AGENT_HOST", "127.0.0.1")
AGENT_PORT = int(os.environ.get("VISION_AGENT_PORT", "8790"))

# IA (OpenAI-compatible)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_VISION_MODEL = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o-mini")

# Loop do agente — settle pós-ação (aguardar UI), não “acelerador de tap”.
# VISION_AGENT_STEP_DELAY: multiplicador (default 1.0). Valor legado "0.45" → escala 1.0.
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
# Alias legado para imports antigos
AGENT_STEP_DELAY_S = SETTLE_CLICK_S

AGENT_MAX_STEPS = int(os.environ.get("VISION_AGENT_MAX_STEPS", "40"))

# Gestos (scrcpy / ADB)
TAP_DOWN_UP_S = float(os.environ.get("VISION_TAP_DOWN_UP", "0.02"))
SCRCPY_SWIPE_MS = int(os.environ.get("VISION_SCRCPY_SWIPE_MS", "280"))
SCRCPY_SWIPE_STEPS = int(os.environ.get("VISION_SCRCPY_SWIPE_STEPS", "12"))
ADB_SWIPE_MS = int(os.environ.get("VISION_ADB_SWIPE_MS", "300"))
LONG_PRESS_MS = int(os.environ.get("VISION_LONG_PRESS_MS", "600"))

# Stream scrcpy (captura): 0 = resolução nativa; >0 limita o lado maior
SCRCPY_MAX_SIZE = int(os.environ.get("VISION_SCRCPY_MAX_SIZE", "0"))
SCRCPY_VIDEO_BIT_RATE = int(os.environ.get("VISION_SCRCPY_BITRATE", "8000000"))
SCRCPY_MAX_FPS = int(os.environ.get("VISION_SCRCPY_MAX_FPS", "60"))
# auto|scrcpy|adb — captura do agente
CAPTURE_BACKEND = (os.environ.get("VISION_CAPTURE_BACKEND") or "auto").lower()


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
