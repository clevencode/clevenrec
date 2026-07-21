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
