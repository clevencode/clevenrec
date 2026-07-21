"""Loop de missão: captura → filtro → IA → execução."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import cv2

from .brain import VisionBrain
from .capture import AdbFrameSource
from .config import (
    AGENT_MAX_STEPS,
    AGENT_STEP_DELAY_S,
    FRAMES_DIR,
    JPEG_QUALITY,
    resolve_adb_path,
)
from .executor import ActionExecutor
from .filter import StabilityFilter
from .normalize import normalize_frame


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AgentState:
    running: bool = False
    objetivo: str = ""
    status: str = "idle"  # idle|running|concluido|bloqueado|error|stopped
    step: int = 0
    backend: str = ""
    last_pensamento: str = ""
    last_acao: str = ""
    last_frame: str = ""
    last_decision: dict[str, Any] = field(default_factory=dict)
    last_exec: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    log: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "objetivo": self.objetivo,
            "status": self.status,
            "step": self.step,
            "backend": self.backend,
            "last_pensamento": self.last_pensamento,
            "last_acao": self.last_acao,
            "last_frame": self.last_frame,
            "last_decision": self.last_decision,
            "last_exec": self.last_exec,
            "message": self.message,
            "log": self.log[-40:],
        }


class AgentMission:
    def __init__(self) -> None:
        self.state = AgentState()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def _push_log(self, event: str, **extra: Any) -> None:
        entry = {"ts": _now(), "event": event, **extra}
        with self._lock:
            self.state.log.append(entry)
            if len(self.state.log) > 200:
                self.state.log = self.state.log[-200:]

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            return self.state.to_dict()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        with self._lock:
            if self.state.running:
                self.state.status = "stopped"
                self.state.message = "Parada solicitada."
                self.state.running = False
        self._push_log("stop")
        return self.get_status()

    def start(self, objetivo: str, serial: Optional[str] = None) -> dict[str, Any]:
        objetivo = (objetivo or "").strip()
        if not objetivo:
            return {"ok": False, "message": "Informe um objetivo."}
        with self._lock:
            if self.state.running:
                return {"ok": False, "message": "Já existe uma missão em andamento."}
            self.state = AgentState(
                running=True,
                objetivo=objetivo,
                status="running",
                message="Missão iniciada.",
            )
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(objetivo, serial),
            daemon=True,
        )
        self._thread.start()
        self._push_log("start", objetivo=objetivo)
        return {"ok": True, **self.get_status()}

    def _save_frame(self, frame) -> Path:
        FRAMES_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")[:-3]
        path = FRAMES_DIR / f"agent_{stamp}.jpg"
        cv2.imwrite(
            str(path),
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), int(JPEG_QUALITY)],
        )
        return path

    def _run(self, objetivo: str, serial: Optional[str]) -> None:
        executor: Optional[ActionExecutor] = None
        try:
            source = AdbFrameSource(
                adb_path=resolve_adb_path(),
                serial=serial,
            )
            serial_used = source.ensure_serial()
            filt = StabilityFilter()
            brain = VisionBrain()
            if not brain.configured:
                raise RuntimeError(
                    "OPENAI_API_KEY ausente. Defina a variável de ambiente."
                )
            executor = ActionExecutor(serial=serial_used)
            with self._lock:
                self.state.backend = executor.backend

            self._push_log(
                "ready",
                serial=serial_used,
                backend=executor.backend,
                scrcpy_error=getattr(executor.scrcpy, "last_error", None),
            )

            idle_rounds = 0
            while not self._stop.is_set() and self.state.step < AGENT_MAX_STEPS:
                raw = source.grab()
                frame = normalize_frame(raw)
                approved, ratio = filt.should_approve(frame)
                if not approved:
                    idle_rounds += 1
                    if idle_rounds >= 3:
                        # força análise mesmo sem grande mudança
                        approved = True
                        filt._last_approved = frame.copy()
                    else:
                        time.sleep(AGENT_STEP_DELAY_S)
                        continue

                idle_rounds = 0
                path = self._save_frame(frame)
                with self._lock:
                    self.state.last_frame = str(path)
                    self.state.step += 1
                    step = self.state.step

                decision = brain.decide(objetivo, path)
                with self._lock:
                    self.state.last_decision = decision
                    self.state.last_pensamento = decision.get("pensamento") or ""
                    self.state.last_acao = decision.get("acao") or ""
                    self.state.status = decision.get("status") or "em_andamento"

                self._push_log(
                    "decision",
                    step=step,
                    ratio=round(ratio, 5),
                    decision=decision,
                )

                acao = (decision.get("acao") or "").lower()
                status = (decision.get("status") or "").lower()
                if acao == "concluido" or status == "concluido":
                    with self._lock:
                        self.state.status = "concluido"
                        self.state.message = "Objetivo concluído."
                        self.state.running = False
                    self._push_log("concluido", step=step)
                    break
                if status == "bloqueado":
                    with self._lock:
                        self.state.status = "bloqueado"
                        self.state.message = decision.get("pensamento") or "Bloqueado."
                        self.state.running = False
                    self._push_log("bloqueado", step=step)
                    break

                exec_result = executor.execute(decision)
                with self._lock:
                    self.state.last_exec = exec_result
                    if exec_result.get("backend"):
                        self.state.backend = exec_result["backend"]
                self._push_log("exec", step=step, result=exec_result)
                time.sleep(AGENT_STEP_DELAY_S)
            else:
                if not self._stop.is_set():
                    with self._lock:
                        self.state.status = "error"
                        self.state.message = "Limite de passos atingido."
                        self.state.running = False
                    self._push_log("max_steps")
        except Exception as exc:
            with self._lock:
                self.state.status = "error"
                self.state.message = str(exc)
                self.state.running = False
            self._push_log("error", message=str(exc))
        finally:
            if executor:
                executor.close()
            with self._lock:
                self.state.running = False


# Singleton usado pelo server
mission = AgentMission()
