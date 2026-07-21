"""Fontes de frame — ADB otimizado (raw / gzip / PNG)."""

from __future__ import annotations

import gzip
import shutil
import struct
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .config import resolve_adb_path

# Android PixelFormat (screencap raw header)
_PIXEL_FORMAT_RGBA_8888 = 1
_PIXEL_FORMAT_RGBX_8888 = 2
_PIXEL_FORMAT_RGB_888 = 3
_PIXEL_FORMAT_RGB_565 = 4


class FrameSource(ABC):
    """Interface para trocar ADB por stream scrcpy no futuro."""

    @abstractmethod
    def grab(self) -> np.ndarray:
        """Retorna frame BGR (H, W, 3)."""


def _decode_raw_screencap(data: bytes) -> np.ndarray:
    """Decodifica saída de `screencap` (sem -p): header LE + pixels."""
    if len(data) < 12:
        raise RuntimeError("screencap raw muito curto.")
    width, height, fmt = struct.unpack_from("<III", data, 0)
    if width <= 0 or height <= 0 or width > 8192 or height > 8192:
        raise RuntimeError(f"Dimensões raw inválidas: {width}x{height}")

    payload = memoryview(data)[12:]
    if fmt in (_PIXEL_FORMAT_RGBA_8888, _PIXEL_FORMAT_RGBX_8888):
        bpp = 4
        needed = width * height * bpp
        if len(payload) >= needed and len(payload) == needed:
            rgba = np.frombuffer(payload, dtype=np.uint8, count=needed).reshape(
                (height, width, 4)
            )
        elif len(payload) >= height * bpp:
            # stride (bytes por linha) pode ser > width*4
            stride = len(payload) // height
            if stride < width * bpp or (stride % bpp) != 0:
                raise RuntimeError(
                    f"Stride raw inválido: len={len(payload)} h={height}"
                )
            row = np.frombuffer(payload, dtype=np.uint8, count=stride * height).reshape(
                (height, stride)
            )
            rgba = row[:, : width * bpp].reshape((height, width, 4))
        else:
            raise RuntimeError("Payload RGBA insuficiente.")
        return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)

    if fmt == _PIXEL_FORMAT_RGB_888:
        bpp = 3
        needed = width * height * bpp
        if len(payload) < needed:
            raise RuntimeError("Payload RGB insuficiente.")
        rgb = np.frombuffer(payload, dtype=np.uint8, count=needed).reshape(
            (height, width, 3)
        )
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    if fmt == _PIXEL_FORMAT_RGB_565:
        needed = width * height * 2
        if len(payload) < needed:
            raise RuntimeError("Payload RGB565 insuficiente.")
        arr = np.frombuffer(payload, dtype=np.uint16, count=width * height).reshape(
            (height, width)
        )
        r = ((arr >> 11) & 0x1F) << 3
        g = ((arr >> 5) & 0x3F) << 2
        b = (arr & 0x1F) << 3
        rgb = np.dstack((r, g, b)).astype(np.uint8)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    raise RuntimeError(f"Formato screencap não suportado: {fmt}")


class AdbFrameSource(FrameSource):
    def __init__(
        self,
        adb_path: Optional[str] = None,
        serial: Optional[str] = None,
        timeout_s: float = 15.0,
        mode: str = "auto",
    ) -> None:
        """
        mode:
          - auto: raw → gzip raw → PNG
          - raw / gzip / png: força um caminho
        """
        self.adb_path = adb_path or resolve_adb_path()
        self.serial = serial
        self.timeout_s = timeout_s
        self.mode = (mode or "auto").lower()
        self.last_mode: Optional[str] = None
        path = Path(self.adb_path)
        if path.is_absolute() and not path.is_file():
            raise FileNotFoundError(f"adb não encontrado: {self.adb_path}")
        if not path.is_absolute() and shutil.which(self.adb_path) is None and not path.is_file():
            # ainda pode estar no PATH relativo ao cwd; deixa falhar no primeiro comando
            pass

    def _base_cmd(self) -> list[str]:
        cmd = [self.adb_path]
        if self.serial:
            cmd.extend(["-s", self.serial])
        return cmd

    def list_devices(self) -> list[str]:
        result = subprocess.run(
            [self.adb_path, "devices"],
            capture_output=True,
            text=True,
            timeout=self.timeout_s,
            check=False,
        )
        devices: list[str] = []
        for line in (result.stdout or "").splitlines()[1:]:
            line = line.strip()
            if not line or "\t" not in line:
                continue
            serial, state = line.split("\t", 1)
            if state.strip() == "device":
                devices.append(serial.strip())
        return devices

    def ensure_serial(self) -> str:
        if self.serial:
            return self.serial
        devices = self.list_devices()
        if not devices:
            raise RuntimeError(
                "Nenhum dispositivo ADB online. Conecte USB ou adb connect IP:5555."
            )
        self.serial = devices[0]
        return self.serial

    def _exec_out(self, *remote: str) -> bytes:
        cmd = self._base_cmd() + ["exec-out", *remote]
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=self.timeout_s,
            check=False,
        )
        if result.returncode != 0 or not result.stdout:
            err = (result.stderr or b"").decode("utf-8", errors="replace")[:240]
            raise RuntimeError(f"Falha exec-out {' '.join(remote)}: {err or 'sem dados'}")
        return result.stdout

    def _grab_raw(self) -> np.ndarray:
        data = self._exec_out("screencap")
        frame = _decode_raw_screencap(data)
        self.last_mode = "raw"
        return frame

    def _grab_gzip(self) -> np.ndarray:
        # raw no device + gzip -1 (rápido) + gunzip local
        data = self._exec_out("sh", "-c", "screencap | gzip -1")
        try:
            raw = gzip.decompress(data)
        except OSError as exc:
            raise RuntimeError(f"gzip inválido: {exc}") from exc
        frame = _decode_raw_screencap(raw)
        self.last_mode = "gzip"
        return frame

    def _grab_png(self) -> np.ndarray:
        data = self._exec_out("screencap", "-p")
        arr = np.frombuffer(data, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError("Não foi possível decodificar o PNG do screencap.")
        self.last_mode = "png"
        return frame

    def grab(self) -> np.ndarray:
        self.ensure_serial()
        if self.mode == "raw":
            return self._grab_raw()
        if self.mode == "gzip":
            return self._grab_gzip()
        if self.mode == "png":
            return self._grab_png()

        # auto: no cabo, gzip costuma vencer raw (menos bytes na USB)
        errors: list[str] = []
        for name, fn in (
            ("gzip", self._grab_gzip),
            ("raw", self._grab_raw),
            ("png", self._grab_png),
        ):
            try:
                return fn()
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        raise RuntimeError("Falha no screencap ADB: " + " | ".join(errors))
