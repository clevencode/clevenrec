"""Execução rápida de ações — scrcpy control socket + fallback ADB."""

from __future__ import annotations

import socket
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .config import (
    CANONICAL_HEIGHT,
    CANONICAL_WIDTH,
    resolve_adb_path,
    resolve_scrcpy_dir,
)

# scrcpy control
TYPE_INJECT_TOUCH = 2
ACTION_DOWN = 0
ACTION_UP = 1
ACTION_MOVE = 2
POINTER_ID = 0x1234567887654321


def _run(cmd: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


class AdbInputFallback:
    """Fallback via adb shell input (mais lento)."""

    def __init__(self, adb_path: str, serial: str) -> None:
        self.adb_path = adb_path
        self.serial = serial
        self._size: Optional[tuple[int, int]] = None

    def _cmd(self, *args: str) -> list[str]:
        return [self.adb_path, "-s", self.serial, "shell", *args]

    def device_size(self) -> tuple[int, int]:
        if self._size:
            return self._size
        r = _run(self._cmd("wm", "size"))
        text = (r.stdout or b"").decode("utf-8", errors="replace")
        # Physical size: 720x1600
        for line in text.splitlines():
            if "x" in line.lower():
                part = line.split(":")[-1].strip()
                if "x" in part:
                    w, h = part.lower().split("x")
                    self._size = (int(w.strip()), int(h.strip()))
                    return self._size
        self._size = (CANONICAL_WIDTH, CANONICAL_HEIGHT)
        return self._size

    def tap(self, x: int, y: int) -> None:
        _run(self._cmd("input", "tap", str(x), str(y)))

    def long_press(self, x: int, y: int, ms: int = 600) -> None:
        _run(self._cmd("input", "swipe", str(x), str(y), str(x), str(y), str(ms)))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        _run(
            self._cmd(
                "input",
                "swipe",
                str(x1),
                str(y1),
                str(x2),
                str(y2),
                str(duration_ms),
            )
        )

    def write_text(self, text: str) -> None:
        # ADB input text: espaços como %s, sem acentos complexos
        encoded = (
            text.replace("\\", "\\\\")
            .replace(" ", "%s")
            .replace("'", "\\'")
        )
        _run(self._cmd("input", "text", encoded))


class ScrcpyController:
    """
    Cliente mínimo scrcpy: vídeo drenado em thread + toques no control socket.
    Se falhar o handshake, ActionExecutor cai no ADB.
    """

    def __init__(
        self,
        adb_path: str,
        serial: str,
        scrcpy_dir: Optional[Path] = None,
        port: int = 27183,
    ) -> None:
        self.adb_path = adb_path
        self.serial = serial
        self.scrcpy_dir = scrcpy_dir or resolve_scrcpy_dir()
        self.port = port
        self._server_proc: Optional[subprocess.Popen] = None
        self._video_sock: Optional[socket.socket] = None
        self._control_sock: Optional[socket.socket] = None
        self._drain_thread: Optional[threading.Thread] = None
        self._stop_drain = threading.Event()
        self._device_w = CANONICAL_WIDTH
        self._device_h = CANONICAL_HEIGHT
        self._connected = False
        self.last_error: Optional[str] = None

    @property
    def connected(self) -> bool:
        return self._connected and self._control_sock is not None

    def _adb(self, *args: str, timeout: float = 20.0) -> subprocess.CompletedProcess:
        return _run([self.adb_path, "-s", self.serial, *args], timeout=timeout)

    def _find_server_jar(self) -> Path:
        if not self.scrcpy_dir:
            raise FileNotFoundError("Pasta do scrcpy não encontrada.")
        for name in ("scrcpy-server", "scrcpy-server.jar"):
            p = self.scrcpy_dir / name
            if p.is_file():
                return p
        raise FileNotFoundError(f"scrcpy-server não encontrado em {self.scrcpy_dir}")

    def _read_device_size(self) -> None:
        r = self._adb("shell", "wm", "size")
        text = (r.stdout or b"").decode("utf-8", errors="replace")
        for line in text.splitlines():
            if "x" in line.lower():
                part = line.split(":")[-1].strip()
                if "x" in part:
                    w, h = part.lower().split("x")
                    self._device_w, self._device_h = int(w.strip()), int(h.strip())
                    return

    def connect(self) -> bool:
        """Sobe scrcpy-server e abre sockets. Retorna True se control OK."""
        self.close()
        try:
            self._read_device_size()
            server = self._find_server_jar()
            # push server
            self._adb("push", str(server), "/data/local/tmp/scrcpy-server.jar")
            self._adb("reverse", "--remove", f"localabstract:scrcpy", timeout=5)
            self._adb("reverse", f"localabstract:scrcpy", f"tcp:{self.port}")

            # Listen before starting server
            listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listen.bind(("127.0.0.1", self.port))
            listen.listen(2)
            listen.settimeout(12.0)

            # scrcpy 2.4+/3+/4 server args vary — try common form
            version = self._guess_version()
            server_cmd = (
                f"CLASSPATH=/data/local/tmp/scrcpy-server.jar "
                f"app_process / com.genymobile.scrcpy.Server {version} "
                f"tunnel_forward=false audio=false control=true "
                f"cleanup=false raw_stream=false max_size=0"
            )
            # Older servers want positional args — also try bare version
            self._server_proc = subprocess.Popen(
                [
                    self.adb_path,
                    "-s",
                    self.serial,
                    "shell",
                    server_cmd,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            self._video_sock = listen.accept()[0]
            self._video_sock.settimeout(5.0)
            self._control_sock = listen.accept()[0]
            self._control_sock.settimeout(5.0)
            self._control_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            listen.close()

            # Drain device meta / video so buffer doesn't block
            self._stop_drain.clear()
            self._drain_thread = threading.Thread(
                target=self._drain_video, daemon=True
            )
            self._drain_thread.start()
            time.sleep(0.15)
            self._connected = True
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self._connected = False
            self.close()
            return False

    def _guess_version(self) -> str:
        # scrcpy.exe -v often prints "scrcpy 2.4" / "3.1" / "3.2" / "4.0"
        if self.scrcpy_dir:
            exe = self.scrcpy_dir / "scrcpy.exe"
            if exe.is_file():
                r = _run([str(exe), "-v"], timeout=5)
                out = ((r.stdout or b"") + (r.stderr or b"")).decode(
                    "utf-8", errors="replace"
                )
                for token in out.replace(",", " ").split():
                    if token[0].isdigit() and "." in token:
                        return token.strip()
        return "3.1"

    def _drain_video(self) -> None:
        sock = self._video_sock
        if not sock:
            return
        try:
            sock.settimeout(0.5)
            while not self._stop_drain.is_set():
                try:
                    data = sock.recv(65536)
                    if not data:
                        break
                except socket.timeout:
                    continue
                except OSError:
                    break
        finally:
            pass

    def _pack_touch(
        self,
        action: int,
        x: int,
        y: int,
        pressure: float = 1.0,
    ) -> bytes:
        pressure_u16 = int(max(0.0, min(1.0, pressure)) * 0xFFFF) & 0xFFFF
        # type(u8) + action(u8) + pointer(u64) + x(i32) + y(i32)
        # + screenW(u16) + screenH(u16) + pressure(u16) + actionButton(i32) + buttons(i32)
        return struct.pack(
            ">BBqiiHHHii",
            TYPE_INJECT_TOUCH,
            action,
            POINTER_ID,
            int(x),
            int(y),
            int(self._device_w),
            int(self._device_h),
            pressure_u16,
            1 if action == ACTION_DOWN else 0,
            1 if action != ACTION_UP else 0,
        )

    def tap(self, x: int, y: int) -> None:
        if not self._control_sock:
            raise RuntimeError("scrcpy control não conectado")
        self._control_sock.sendall(self._pack_touch(ACTION_DOWN, x, y, 1.0))
        time.sleep(0.02)
        self._control_sock.sendall(self._pack_touch(ACTION_UP, x, y, 0.0))

    def long_press(self, x: int, y: int, ms: int = 600) -> None:
        if not self._control_sock:
            raise RuntimeError("scrcpy control não conectado")
        self._control_sock.sendall(self._pack_touch(ACTION_DOWN, x, y, 1.0))
        time.sleep(ms / 1000.0)
        self._control_sock.sendall(self._pack_touch(ACTION_UP, x, y, 0.0))

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 280,
        steps: int = 12,
    ) -> None:
        if not self._control_sock:
            raise RuntimeError("scrcpy control não conectado")
        self._control_sock.sendall(self._pack_touch(ACTION_DOWN, x1, y1, 1.0))
        dt = duration_ms / 1000.0 / max(steps, 1)
        for i in range(1, steps + 1):
            t = i / steps
            x = int(x1 + (x2 - x1) * t)
            y = int(y1 + (y2 - y1) * t)
            self._control_sock.sendall(self._pack_touch(ACTION_MOVE, x, y, 1.0))
            time.sleep(dt)
        self._control_sock.sendall(self._pack_touch(ACTION_UP, x2, y2, 0.0))

    def close(self) -> None:
        self._connected = False
        self._stop_drain.set()
        for sock in (self._control_sock, self._video_sock):
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
        self._control_sock = None
        self._video_sock = None
        if self._server_proc and self._server_proc.poll() is None:
            try:
                self._server_proc.terminate()
            except OSError:
                pass
        self._server_proc = None
        try:
            self._adb("reverse", "--remove", "localabstract:scrcpy", timeout=3)
        except Exception:
            pass


def map_canonical_to_device(
    x: int,
    y: int,
    device_w: int,
    device_h: int,
    canon_w: int = CANONICAL_WIDTH,
    canon_h: int = CANONICAL_HEIGHT,
) -> tuple[int, int]:
    nx = int(x * device_w / canon_w)
    ny = int(y * device_h / canon_h)
    nx = max(0, min(device_w - 1, nx))
    ny = max(0, min(device_h - 1, ny))
    return nx, ny


class ActionExecutor:
    """Executa decisões JSON do brain (coords canônicas 1080x1920)."""

    def __init__(
        self,
        serial: Optional[str] = None,
        adb_path: Optional[str] = None,
        prefer_scrcpy: bool = True,
    ) -> None:
        self.adb_path = adb_path or resolve_adb_path()
        self.serial = serial or self._first_serial()
        self.adb = AdbInputFallback(self.adb_path, self.serial)
        self.scrcpy = ScrcpyController(self.adb_path, self.serial)
        self.prefer_scrcpy = prefer_scrcpy
        self.backend = "adb"
        if prefer_scrcpy and self.scrcpy.connect():
            self.backend = "scrcpy"
        self.device_w, self.device_h = self.adb.device_size()

    def _first_serial(self) -> str:
        r = _run([self.adb_path, "devices"])
        text = (r.stdout or b"").decode("utf-8", errors="replace")
        for line in text.splitlines()[1:]:
            line = line.strip()
            if "\tdevice" in line:
                return line.split("\t")[0].strip()
        raise RuntimeError("Nenhum dispositivo ADB online.")

    def _xy(self, decision: dict[str, Any]) -> tuple[int, int]:
        coords = decision.get("coordenadas") or {}
        x = int(coords.get("x", CANONICAL_WIDTH // 2))
        y = int(coords.get("y", CANONICAL_HEIGHT // 2))
        return map_canonical_to_device(x, y, self.device_w, self.device_h)

    def _touch_backend(self):
        if self.backend == "scrcpy" and self.scrcpy.connected:
            return self.scrcpy
        return self.adb

    def execute(self, decision: dict[str, Any]) -> dict[str, Any]:
        acao = (decision.get("acao") or "").strip().lower()
        result: dict[str, Any] = {
            "ok": True,
            "acao": acao,
            "backend": self.backend,
        }

        if acao in ("concluido", "aguardar", ""):
            result["skipped"] = True
            return result

        try:
            if acao == "click":
                x, y = self._xy(decision)
                result["xy"] = [x, y]
                try:
                    self._touch_backend().tap(x, y)
                except Exception as exc:
                    if self.backend == "scrcpy":
                        result["backend"] = "adb_fallback"
                        result["scrcpy_error"] = str(exc)
                        self.adb.tap(x, y)
                    else:
                        raise
            elif acao == "long_click":
                x, y = self._xy(decision)
                result["xy"] = [x, y]
                try:
                    self._touch_backend().long_press(x, y)
                except Exception:
                    self.adb.long_press(x, y)
                    result["backend"] = "adb_fallback"
            elif acao == "swipe_up":
                x, y = self._xy(decision)
                x2, y2 = x, max(40, y - int(self.device_h * 0.35))
                result["xy"] = [x, y, x2, y2]
                try:
                    self._touch_backend().swipe(x, y, x2, y2)
                except Exception:
                    self.adb.swipe(x, y, x2, y2)
                    result["backend"] = "adb_fallback"
            elif acao == "swipe_down":
                x, y = self._xy(decision)
                x2, y2 = x, min(self.device_h - 40, y + int(self.device_h * 0.35))
                result["xy"] = [x, y, x2, y2]
                try:
                    self._touch_backend().swipe(x, y, x2, y2)
                except Exception:
                    self.adb.swipe(x, y, x2, y2)
                    result["backend"] = "adb_fallback"
            elif acao == "write_text":
                text = decision.get("texto_input") or ""
                self.adb.write_text(str(text))
                result["backend"] = "adb"
                result["texto"] = text
            else:
                result["ok"] = False
                result["message"] = f"Ação desconhecida: {acao}"
        except Exception as exc:
            result["ok"] = False
            result["message"] = str(exc)

        return result

    def close(self) -> None:
        self.scrcpy.close()
