"""Autoaperfeiçoamento de missão: gravar ecrã → sucesso → apertar timing → repetir.

Estrutura:
  1. Executa passos gravando a tela (adb screenrecord)
  2. Se cumpriu a tarefa (FINAL_OK), grava artefacto + aplica timing do sucesso
  3. Tenta de novo com base no sucesso anterior
  4. Para quando elapsed ≤ target (velocidade razoável) ou esgota rounds

Uso:
  python -u -m vision_agent.yv_status_som_test --auto
  VISION_AUTO_TARGET_S=90 VISION_AUTO_ROUNDS=5 ...
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

TIMING_KEYS: tuple[str, ...] = (
    "yv_open_s",
    "after_long_s",
    "wa_open_s",
    "after_tap_s",
    "after_paste_s",
    "after_panel_s",
    "after_send_s",
    "scroll_s",
    "retry_s",
)

TIMING_FLOOR: dict[str, float] = {
    "yv_open_s": 0.85,
    "after_long_s": 0.16,
    "wa_open_s": 0.28,
    "after_tap_s": 0.10,
    "after_paste_s": 0.20,
    "after_panel_s": 0.08,
    "after_send_s": 0.40,
    "scroll_s": 0.10,
    "retry_s": 0.22,
}

SHRINK_FACTOR = 0.90


def _recs_dir() -> Path:
    d = Path(__file__).resolve().parent / "frames" / "auto_improve"
    d.mkdir(parents=True, exist_ok=True)
    return d


class ScreenRecorder:
    """Grava ecrã via adb screenrecord enquanto a missão corre."""

    def __init__(
        self,
        adb: str,
        serial: str,
        remote_path: str = "/sdcard/yv_auto.mp4",
    ):
        self.adb = adb
        self.serial = serial
        self.remote = remote_path
        self.proc: Optional[subprocess.Popen] = None

    def start(self, time_limit_s: int = 180) -> None:
        try:
            subprocess.run(
                [self.adb, "-s", self.serial, "shell", "rm", "-f", self.remote],
                capture_output=True,
                timeout=8,
            )
        except Exception:
            pass
        self.proc = subprocess.Popen(
            [
                self.adb,
                "-s",
                self.serial,
                "shell",
                "screenrecord",
                "--time-limit",
                str(time_limit_s),
                self.remote,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.35)
        print(f"REC start {self.remote}", flush=True)

    def stop_and_pull(self, local: Path, *, keep: bool) -> Optional[Path]:
        if self.proc and self.proc.poll() is None:
            try:
                subprocess.run(
                    [
                        self.adb,
                        "-s",
                        self.serial,
                        "shell",
                        "pkill",
                        "-l",
                        "INT",
                        "screenrecord",
                    ],
                    capture_output=True,
                    timeout=5,
                )
            except Exception:
                pass
            try:
                self.proc.wait(timeout=4)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        time.sleep(0.4)
        if not keep:
            print("REC discard (missao falhou)", flush=True)
            return None
        local.parent.mkdir(parents=True, exist_ok=True)
        try:
            r = subprocess.run(
                [self.adb, "-s", self.serial, "pull", self.remote, str(local)],
                capture_output=True,
                timeout=60,
            )
            if r.returncode == 0 and local.exists():
                print(f"REC saved {local} ({local.stat().st_size} B)", flush=True)
                return local
            err = r.stderr.decode("utf-8", "replace")[:200]
            print(f"REC pull falhou {err}", flush=True)
        except Exception as exc:
            print(f"REC pull err {exc}", flush=True)
        return None


def shrink_timing(
    timing: dict[str, Any], *, factor: float = SHRINK_FACTOR
) -> dict[str, float]:
    out: dict[str, float] = {}
    for k in TIMING_KEYS:
        if k not in timing:
            continue
        cur = float(timing[k])
        floor = TIMING_FLOOR.get(k, cur * 0.5)
        out[k] = round(max(floor, cur * factor), 3)
    return out


def apply_timing(cache: dict, new_timing: dict[str, float]) -> None:
    t = cache.setdefault("timing", {})
    t.update(new_timing)


def snapshot_timing(cache: dict) -> dict[str, float]:
    t = cache.get("timing") or {}
    return {k: float(t[k]) for k in TIMING_KEYS if k in t}


def save_auto_log(entry: dict) -> Path:
    path = _recs_dir() / "auto_log.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def run_auto_improve(
    run_once: Callable[[], tuple[bool, float, dict]],
    *,
    adb: str,
    serial: str,
    cache: dict,
    cache_path: Path,
    target_s: float | None = None,
    max_rounds: int | None = None,
) -> dict[str, Any]:
    """Loop: gravar → executar → se ok, apertar timing e repetir ate target_s."""
    target = float(
        target_s
        if target_s is not None
        else os.environ.get("VISION_AUTO_TARGET_S", "90")
    )
    rounds = int(
        max_rounds
        if max_rounds is not None
        else os.environ.get("VISION_AUTO_ROUNDS", "5")
    )
    best: Optional[dict] = None
    last_good_timing = snapshot_timing(cache)

    print(
        f"AUTO_IMPROVE target<={target}s rounds<={rounds} serial={serial}",
        flush=True,
    )

    for i in range(1, rounds + 1):
        print(
            f"\n===== AUTO round {i}/{rounds} timing={snapshot_timing(cache)} =====",
            flush=True,
        )
        rec = ScreenRecorder(adb, serial)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        local_mp4 = _recs_dir() / f"round{i}_{stamp}.mp4"
        rec.start(time_limit_s=min(180, int(target * 2.5) + 60))
        try:
            ok, elapsed, meta = run_once()
        except Exception as exc:
            print(f"AUTO round {i} EXCEPTION {exc}", flush=True)
            rec.stop_and_pull(local_mp4, keep=False)
            apply_timing(cache, last_good_timing)
            save_auto_log(
                {
                    "round": i,
                    "ok": False,
                    "error": str(exc),
                    "timing": snapshot_timing(cache),
                    "ts": stamp,
                }
            )
            continue

        video = rec.stop_and_pull(local_mp4, keep=ok)
        entry = {
            "round": i,
            "ok": ok,
            "elapsed_s": elapsed,
            "target_s": target,
            "timing": snapshot_timing(cache),
            "video": str(video) if video else None,
            "meta": meta,
            "ts": stamp,
        }
        save_auto_log(entry)
        print(
            f"AUTO round {i} ok={ok} elapsed={elapsed}s "
            f"best={(best or {}).get('elapsed_s')} target={target}",
            flush=True,
        )

        if ok:
            last_good_timing = snapshot_timing(cache)
            if best is None or elapsed < best["elapsed_s"]:
                best = dict(entry)
            if elapsed <= target:
                print(
                    f"AUTO DONE velocidade razoavel {elapsed}s <= {target}s",
                    flush=True,
                )
                cache["auto_improve"] = {
                    "done": True,
                    "best_elapsed_s": elapsed,
                    "target_s": target,
                    "rounds": i,
                    "timing": snapshot_timing(cache),
                }
                cache_path.write_text(
                    json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                return {
                    "ok": True,
                    "best": best,
                    "rounds": i,
                    "hit_target": True,
                }

            # ultimo round: nao apertar mais — guarda o melhor timing
            if i >= rounds:
                break

            shrunk = shrink_timing(cache.get("timing") or {}, factor=SHRINK_FACTOR)
            apply_timing(cache, shrunk)
            cache["notes"] = (
                f"auto_improve round{i} ok {elapsed}s -> shrink "
                f"next={snapshot_timing(cache)}"
            )
            cache_path.write_text(
                json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(
                f"AUTO shrink timing (sucesso {elapsed}s) -> {snapshot_timing(cache)}",
                flush=True,
            )
            time.sleep(1.0)
            continue

        print("AUTO miss — restaura timing do ultimo sucesso", flush=True)
        apply_timing(cache, last_good_timing)
        cache_path.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        time.sleep(1.0)

    hit = bool(best and best["elapsed_s"] <= target)
    print(
        f"AUTO END rounds={rounds} best={best} hit_target={hit}",
        flush=True,
    )
    if best:
        # restaura timing do melhor sucesso (nao o ultimo shrink falhado/lento)
        best_t = best.get("timing") or last_good_timing
        apply_timing(cache, best_t)
        cache["auto_improve"] = {
            "done": hit,
            "best_elapsed_s": best["elapsed_s"],
            "target_s": target,
            "rounds": rounds,
            "timing": best_t,
            "video": best.get("video"),
        }
        cache["notes"] = (
            f"auto_improve best={best['elapsed_s']}s "
            f"target={target}s hit={hit}"
        )
        cache_path.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"AUTO restore best timing {best_t}", flush=True)
    return {
        "ok": bool(best),
        "best": best,
        "rounds": rounds,
        "hit_target": hit,
    }
