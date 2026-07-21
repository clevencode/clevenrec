"""Execução rápida de ações — scrcpy control socket + fallback ADB."""

from __future__ import annotations

import socket
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .config import (
    ADB_SWIPE_MS,
    CANONICAL_HEIGHT,
    CANONICAL_WIDTH,
    LONG_PRESS_MS,
    SCRCPY_MAX_FPS,
    SCRCPY_MAX_SIZE,
    SCRCPY_SWIPE_MS,
    SCRCPY_SWIPE_STEPS,
    SCRCPY_VIDEO_BIT_RATE,
    TAP_DOWN_UP_S,
    resolve_adb_path,
    resolve_scrcpy_dir,
)

# scrcpy 2+/3+/4 video codec ids (big-endian fourcc-ish)
_VIDEO_CODECS = {
    0x68323634: "h264",
    0x68323635: "hevc",
    0x00617631: "av1",
    0x00767038: "vp8",
    0x00767039: "vp9",
}

# scrcpy control (ordem do enum em control_msg.h)
TYPE_INJECT_KEYCODE = 0
TYPE_INJECT_TEXT = 1
TYPE_INJECT_TOUCH = 2
TYPE_SET_CLIPBOARD = 9
ACTION_DOWN = 0
ACTION_UP = 1
ACTION_MOVE = 2
POINTER_ID = 0x1234567887654321
# Limite do protocolo scrcpy para INJECT_TEXT
INJECT_TEXT_MAX_LEN = 300


def _run(cmd: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


class AdbShellSession:
    """
    Uma sessão `adb shell` persistente: injeta comandos via stdin
    sem spawnar um novo processo ADB por toque.
    """

    def __init__(self, adb_path: str, serial: str) -> None:
        self.adb_path = adb_path
        self.serial = serial
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._seq = 0
        self._open()

    def _open(self) -> None:
        self.close(quiet=True)
        self._proc = subprocess.Popen(
            [self.adb_path, "-s", self.serial, "shell"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )

    def run(self, command: str, wait: bool = True, timeout: float = 8.0) -> None:
        """
        Envia comando no shell aberto.
        wait=True: bloqueia até o device terminar (echo de marcador).
        """
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._open()
            assert self._proc is not None and self._proc.stdin is not None
            if not wait:
                line = (command.rstrip("\n") + "\n").encode("utf-8", errors="replace")
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
                return

            self._seq += 1
            marker = f"__CLEVEN_OK_{self._seq}__"
            # um único processo shell; input ainda sobe Java no device,
            # mas sem spawn de adb.exe no host
            payload = f"{command.rstrip()}; echo {marker}\n".encode(
                "utf-8", errors="replace"
            )
            self._proc.stdin.write(payload)
            self._proc.stdin.flush()
            assert self._proc.stdout is not None
            deadline = time.time() + timeout
            needle = marker.encode()
            buf = b""
            while time.time() < deadline:
                chunk = self._proc.stdout.read(256)
                if not chunk:
                    if self._proc.poll() is not None:
                        raise RuntimeError("adb shell encerrou durante comando.")
                    time.sleep(0.002)
                    continue
                buf += chunk
                if needle in buf:
                    return
                if len(buf) > 65536:
                    buf = buf[-4096:]
            raise TimeoutError(f"Timeout ADB shell: {command[:80]}")

    def close(self, quiet: bool = False) -> None:
        proc = self._proc
        self._proc = None
        if not proc:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                try:
                    proc.stdin.write(b"exit\n")
                    proc.stdin.flush()
                except OSError:
                    pass
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            if not quiet:
                raise


class AdbInputFallback:
    """Fallback ADB com shell persistente (evita spawn por clique)."""

    def __init__(self, adb_path: str, serial: str) -> None:
        self.adb_path = adb_path
        self.serial = serial
        self._size: Optional[tuple[int, int]] = None
        self.shell = AdbShellSession(adb_path, serial)

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
        self.shell.run(f"input tap {int(x)} {int(y)}")

    def long_press(self, x: int, y: int, ms: int | None = None) -> None:
        hold = LONG_PRESS_MS if ms is None else ms
        self.shell.run(
            f"input swipe {int(x)} {int(y)} {int(x)} {int(y)} {int(hold)}"
        )

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int | None = None,
    ) -> None:
        dur = ADB_SWIPE_MS if duration_ms is None else duration_ms
        self.shell.run(
            f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(dur)}"
        )

    def write_text(self, text: str) -> None:
        # ADB input text: espaços como %s. Lista argv evita quebra do shell
        # por (, ), &, |, etc. Acentos costumam falhar no input text.
        encoded = (
            text.replace("\\", "\\\\")
            .replace(" ", "%s")
            .replace("'", "")
            .replace('"', "")
            .replace("(", "")
            .replace(")", "")
            .replace("&", "e")
            .replace("|", "")
            .replace(";", "")
            .replace("<", "")
            .replace(">", "")
            .replace("`", "")
            .replace("$", "")
            .replace("\n", "%s")
        )
        # Preferir invocação isolada (mais estável que stdin para strings longas)
        r = _run(self._cmd("input", "text", encoded), timeout=30.0)
        if r.returncode != 0:
            err = (r.stderr or b"").decode("utf-8", errors="replace")[:200]
            # fallback na sessão persistente
            try:
                self.shell.run(f"input text {encoded}")
            except Exception as exc:
                raise RuntimeError(f"input text falhou: {err or exc}") from exc

    def close(self) -> None:
        self.shell.close(quiet=True)

class ScrcpyController:
    """
    Cliente mínimo scrcpy: decodifica vídeo H.264 + toques no control socket.
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
        self.device_name: str = ""
        self.codec_name: str = "h264"
        self.last_frame: Optional[np.ndarray] = None
        self.frame_count = 0
        self._frame_lock = threading.Lock()
        self._frame_event = threading.Event()
        self.video_ok = False
        self._clipboard_seq = 0
        self._control_lock = threading.Lock()

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

    @staticmethod
    def _recv_exact(sock: socket.socket, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Socket vídeo scrcpy fechou.")
            buf.extend(chunk)
        return bytes(buf)

    def connect(self) -> bool:
        """Sobe scrcpy-server e abre sockets. Retorna True se control OK."""
        self.close()
        try:
            self._read_device_size()
            server = self._find_server_jar()
            # push server
            self._adb("push", str(server), "/data/local/tmp/scrcpy-server.jar")
            self._adb("reverse", "--remove", "localabstract:scrcpy", timeout=5)
            self._adb("reverse", "localabstract:scrcpy", f"tcp:{self.port}")

            # Listen before starting server
            listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listen.bind(("127.0.0.1", self.port))
            listen.listen(2)
            listen.settimeout(12.0)

            version = self._guess_version()
            max_size = max(0, SCRCPY_MAX_SIZE)
            server_cmd = (
                f"CLASSPATH=/data/local/tmp/scrcpy-server.jar "
                f"app_process / com.genymobile.scrcpy.Server {version} "
                f"tunnel_forward=false audio=false control=true "
                f"cleanup=false raw_stream=false video_codec=h264 "
                f"max_size={max_size} "
                f"video_bit_rate={SCRCPY_VIDEO_BIT_RATE} "
                f"max_fps={SCRCPY_MAX_FPS}"
            )
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
            self._video_sock.settimeout(8.0)
            self._control_sock = listen.accept()[0]
            self._control_sock.settimeout(5.0)
            self._control_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            listen.close()

            # Protocolo scrcpy 2+/4 (reverse): 1º socket = device name + vídeo
            name_raw = self._recv_exact(self._video_sock, 64)
            self.device_name = name_raw.split(b"\x00", 1)[0].decode(
                "utf-8", errors="replace"
            )
            codec_id = struct.unpack(">I", self._recv_exact(self._video_sock, 4))[0]
            self.codec_name = _VIDEO_CODECS.get(codec_id, "h264")

            self._stop_drain.clear()
            self._frame_event.clear()
            self.last_frame = None
            self.frame_count = 0
            self.video_ok = False
            self._drain_thread = threading.Thread(
                target=self._decode_video, daemon=True, name="scrcpy-video"
            )
            self._drain_thread.start()
            # Aguarda 1º frame (não bloqueia connect se demorar — touch já serve)
            self.wait_frame(timeout=2.5)
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

    def wait_frame(self, timeout: float = 3.0) -> Optional[np.ndarray]:
        """Retorna cópia do último frame BGR (espera até timeout se ainda não houver)."""
        deadline = time.time() + max(0.0, timeout)
        while time.time() < deadline:
            with self._frame_lock:
                if self.last_frame is not None:
                    return self.last_frame.copy()
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            self._frame_event.wait(timeout=min(0.05, remaining))
        with self._frame_lock:
            return None if self.last_frame is None else self.last_frame.copy()

    def _decode_video(self) -> None:
        """Demux 12-byte headers + decode H.264/HEVC → last_frame BGR."""
        sock = self._video_sock
        if not sock:
            return
        try:
            import av
            from av.error import InvalidDataError
        except ImportError:
            self.last_error = "Pacote 'av' (PyAV) ausente — captura scrcpy desativada."
            self._discard_video(sock)
            return

        try:
            codec = av.CodecContext.create(self.codec_name, "r")
            sock.settimeout(0.5)
            while not self._stop_drain.is_set():
                try:
                    header = self._recv_exact(sock, 12)
                except socket.timeout:
                    continue
                except (ConnectionError, OSError):
                    break

                # Session packet (MSB=1): width/height da sessão de captura
                if header[0] & 0x80:
                    w, h = struct.unpack(">II", header[4:12])
                    if w > 0 and h > 0:
                        self._device_w, self._device_h = int(w), int(h)
                    continue

                _pts_flags, packet_size = struct.unpack(">QI", header)
                if packet_size <= 0 or packet_size > 16_000_000:
                    continue
                try:
                    payload = self._recv_exact(sock, packet_size)
                except (ConnectionError, OSError, socket.timeout):
                    break

                try:
                    packets = codec.parse(payload)
                    for packet in packets:
                        for frame in codec.decode(packet):
                            arr = frame.to_ndarray(format="bgr24")
                            with self._frame_lock:
                                self.last_frame = arr
                                self.frame_count += 1
                                self.video_ok = True
                            self._frame_event.set()
                except InvalidDataError:
                    continue
                except Exception as exc:
                    self.last_error = f"decode: {exc}"
                    continue
        finally:
            pass

    def _discard_video(self, sock: socket.socket) -> None:
        """Evita backpressure se o decode não estiver disponível."""
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
        except Exception:
            pass

    def _send_control(self, payload: bytes) -> None:
        if not self._control_sock:
            raise RuntimeError("scrcpy control não conectado")
        with self._control_lock:
            self._control_sock.sendall(payload)

    def inject_text(self, text: str) -> None:
        """Injeta texto Unicode pelo control socket (sem ADB). Máx. 300 chars."""
        raw = (text or "").encode("utf-8")
        if len(raw) > INJECT_TEXT_MAX_LEN:
            raise ValueError(
                f"inject_text limitado a {INJECT_TEXT_MAX_LEN} bytes UTF-8 "
                f"(recebido {len(raw)}). Use clipboard+paste."
            )
        payload = struct.pack(">BI", TYPE_INJECT_TEXT, len(raw)) + raw
        self._send_control(payload)

    def set_clipboard(self, text: str, paste: bool = True) -> None:
        """Define clipboard no device; paste=True cola no campo focado."""
        raw = (text or "").encode("utf-8")
        self._clipboard_seq = (self._clipboard_seq + 1) & 0xFFFFFFFFFFFFFFFF
        # type + sequence(u64) + paste(u8) + length(u32) + utf8
        payload = (
            struct.pack(
                ">BQBI",
                TYPE_SET_CLIPBOARD,
                self._clipboard_seq,
                1 if paste else 0,
                len(raw),
            )
            + raw
        )
        self._send_control(payload)

    def write_text(self, text: str) -> str:
        """
        Texto rápido via scrcpy (sem adb input text).
        Retorna o método usado: inject_text | clipboard_paste.
        """
        text = text or ""
        raw_len = len(text.encode("utf-8"))
        if 0 < raw_len <= INJECT_TEXT_MAX_LEN:
            try:
                self.inject_text(text)
                return "inject_text"
            except Exception:
                self.set_clipboard(text, paste=True)
                return "clipboard_paste"
        self.set_clipboard(text, paste=True)
        return "clipboard_paste"

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
        self._send_control(self._pack_touch(ACTION_DOWN, x, y, 1.0))
        time.sleep(TAP_DOWN_UP_S)
        self._send_control(self._pack_touch(ACTION_UP, x, y, 0.0))

    def long_press(self, x: int, y: int, ms: int | None = None) -> None:
        if not self._control_sock:
            raise RuntimeError("scrcpy control não conectado")
        hold = LONG_PRESS_MS if ms is None else ms
        self._send_control(self._pack_touch(ACTION_DOWN, x, y, 1.0))
        time.sleep(hold / 1000.0)
        self._send_control(self._pack_touch(ACTION_UP, x, y, 0.0))

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int | None = None,
        steps: int | None = None,
    ) -> None:
        if not self._control_sock:
            raise RuntimeError("scrcpy control não conectado")
        dur = SCRCPY_SWIPE_MS if duration_ms is None else duration_ms
        n = SCRCPY_SWIPE_STEPS if steps is None else steps
        self._send_control(self._pack_touch(ACTION_DOWN, x1, y1, 1.0))
        dt = dur / 1000.0 / max(n, 1)
        for i in range(1, n + 1):
            t = i / n
            x = int(x1 + (x2 - x1) * t)
            y = int(y1 + (y2 - y1) * t)
            self._send_control(self._pack_touch(ACTION_MOVE, x, y, 1.0))
            time.sleep(dt)
        self._send_control(self._pack_touch(ACTION_UP, x2, y2, 0.0))

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
        if self.backend == "scrcpy":
            # Toques scrcpy usam o espaço do frame de vídeo
            self.device_w = int(self.scrcpy._device_w)
            self.device_h = int(self.scrcpy._device_h)

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

    def _fallback_to_adb(self, result: dict[str, Any], exc: Exception) -> None:
        """Marca fallback sticky: próximos passos não retentam scrcpy quebrado."""
        self.backend = "adb"
        result["backend"] = "adb_fallback"
        result["scrcpy_error"] = str(exc)

    def _run_touch(self, result: dict[str, Any], fn_name: str, *args: Any) -> None:
        try:
            getattr(self._touch_backend(), fn_name)(*args)
        except Exception as exc:
            if self.backend == "scrcpy":
                self._fallback_to_adb(result, exc)
                getattr(self.adb, fn_name)(*args)
            else:
                raise

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
                self._run_touch(result, "tap", x, y)
            elif acao == "long_click":
                x, y = self._xy(decision)
                result["xy"] = [x, y]
                self._run_touch(result, "long_press", x, y)
            elif acao == "swipe_up":
                x, y = self._xy(decision)
                x2, y2 = x, max(40, y - int(self.device_h * 0.35))
                result["xy"] = [x, y, x2, y2]
                self._run_touch(result, "swipe", x, y, x2, y2)
            elif acao == "swipe_down":
                x, y = self._xy(decision)
                x2, y2 = x, min(self.device_h - 40, y + int(self.device_h * 0.35))
                result["xy"] = [x, y, x2, y2]
                self._run_touch(result, "swipe", x, y, x2, y2)
            elif acao == "write_text":
                text = decision.get("texto_input") or ""
                if self.backend == "scrcpy" and self.scrcpy.connected:
                    try:
                        method = self.scrcpy.write_text(str(text))
                        result["backend"] = f"scrcpy_{method}"
                        result["texto"] = text
                    except Exception as exc:
                        self._fallback_to_adb(result, exc)
                        self.adb.write_text(str(text))
                        result["texto"] = text
                else:
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
        self.adb.close()
