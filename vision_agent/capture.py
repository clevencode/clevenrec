"""Fontes de frame — MVP via adb screencap."""

from __future__ import annotations

import shutil
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .config import resolve_adb_path


class FrameSource(ABC):
    """Interface para trocar ADB por stream scrcpy no futuro."""

    @abstractmethod
    def grab(self) -> np.ndarray:
        """Retorna frame BGR (H, W, 3)."""


class AdbFrameSource(FrameSource):
    def __init__(
        self,
        adb_path: Optional[str] = None,
        serial: Optional[str] = None,
        timeout_s: float = 15.0,
    ) -> None:
        self.adb_path = adb_path or resolve_adb_path()
        self.serial = serial
        self.timeout_s = timeout_s
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

    def grab(self) -> np.ndarray:
        self.ensure_serial()
        cmd = self._base_cmd() + ["exec-out", "screencap", "-p"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=self.timeout_s,
            check=False,
        )
        if result.returncode != 0 or not result.stdout:
            err = (result.stderr or b"").decode("utf-8", errors="replace")[:240]
            raise RuntimeError(f"Falha no screencap ADB: {err or 'sem dados'}")

        data = np.frombuffer(result.stdout, dtype=np.uint8)
        frame = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError("Não foi possível decodificar o PNG do screencap.")
        return frame
