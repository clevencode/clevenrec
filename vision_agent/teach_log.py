"""Gravação teach por eventos (logs), sem vídeo — processamento rápido.

Captura:
  - toques via `adb shell getevent -lt`
  - snapshot UI (labels/aria) após cada toque

Saída: JSONL em vision_agent/frames/teach/events_*.jsonl

Uso:
  python -u -m vision_agent.teach_log
  # terminar: touch vision_agent/frames/teach/.stop   ou Ctrl+C
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vision_agent.config import CANONICAL_HEIGHT, CANONICAL_WIDTH, resolve_adb_path
from vision_agent.precision import map_physical_to_canonical
from vision_agent.som import dump_ui_xml

OUT_DIR = Path(__file__).resolve().parent / "frames" / "teach"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _serial() -> str:
    env = os.environ.get("VISION_ADB_SERIAL")
    if env:
        return env
    adb = resolve_adb_path()
    out = subprocess.run(
        [adb, "devices"], capture_output=True, timeout=8
    ).stdout.decode("utf-8", "replace")
    for line in out.splitlines()[1:]:
        if "\tdevice" in line:
            return line.split("\t", 1)[0].strip()
    return "192.168.1.161:5555"


def _wm_size(adb: str, serial: str) -> tuple[int, int]:
    out = subprocess.run(
        [adb, "-s", serial, "shell", "wm", "size"],
        capture_output=True,
        timeout=8,
    ).stdout.decode("utf-8", "replace")
    m = re.search(r"(\d+)x(\d+)", out)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 720, 1600


def _find_touch_device(adb: str, serial: str) -> str | None:
    out = subprocess.run(
        [adb, "-s", serial, "shell", "getevent", "-pl"],
        capture_output=True,
        timeout=10,
    ).stdout.decode("utf-8", "replace")
    current = None
    best = None
    for line in out.splitlines():
        if line.startswith("add device"):
            m = re.search(r"(/dev/input/event\d+)", line)
            current = m.group(1) if m else None
        if current and "ABS_MT_POSITION_X" in line:
            best = current
    return best


def _marks_from_xml(xml: str, dw: int, dh: int) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()

    def add(text: str, x1: int, y1: int, x2: int, y2: int) -> None:
        text = (text or "").strip()
        if len(text) < 1:
            return
        key = f"{text}|{x1}|{y1}"
        if key in seen:
            return
        seen.add(key)
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        ccx, ccy = map_physical_to_canonical(cx, cy, dw, dh)
        out.append({"label": text[:100], "cx": ccx, "cy": ccy, "phys": [cx, cy]})

    for m in re.finditer(
        r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        xml,
    ):
        x1, y1, x2, y2 = map(int, m.groups())
        # janela em torno do nó para text/content-desc
        start = max(0, m.start() - 400)
        chunk = xml[start : m.end() + 80]
        texts = re.findall(r'(?:text|content-desc)="([^"]+)"', chunk)
        for t in texts:
            if t and t not in ("", "null"):
                add(t, x1, y1, x2, y2)
                break
    return out[:60]


def snapshot_ui(adb: str, serial: str, dw: int, dh: int) -> dict:
    try:
        xml = dump_ui_xml(adb, serial)
        labels = _marks_from_xml(xml, dw, dh)
    except Exception as exc:
        return {"error": str(exc), "labels": [], "n": 0, "blob": ""}
    blob = " | ".join(x["label"] for x in labels)[:500]
    return {"n": len(labels), "labels": labels, "blob": blob}


def _parse_getevent_line(line: str):
    m = re.search(
        r"\[\s*([\d.]+)\]\s+(\S+):\s+(\S+)\s+(\S+)\s+([0-9a-fA-F]+)",
        line,
    )
    if not m:
        return None
    _t, _dev, ev, code, val = m.groups()
    return ev, code, int(val, 16)


def main() -> None:
    adb = resolve_adb_path()
    serial = _serial()
    dw, dh = _wm_size(adb, serial)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    log_path = OUT_DIR / f"events_{stamp}.jsonl"
    stop_path = OUT_DIR / ".stop"
    if stop_path.exists():
        stop_path.unlink()

    touch_dev = _find_touch_device(adb, serial)
    print(
        f"TEACH_LOG serial={serial} {dw}x{dh} touch={touch_dev}",
        flush=True,
    )
    print(f"TEACH_LOG file={log_path}", flush=True)
    print(
        "Faz os passos no telemovel. Quando acabares diz 'feito' (eu crio .stop).",
        flush=True,
    )

    def emit(obj: dict) -> None:
        obj["t"] = round(time.time(), 3)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        kind = obj.get("type")
        if kind == "tap":
            print(
                f"TAP phys=({obj['x']},{obj['y']}) can={obj.get('xy_can')}",
                flush=True,
            )
        elif kind == "ui":
            print(f"UI n={obj.get('n')} {(obj.get('blob') or '')[:90]}", flush=True)
        else:
            print(f"{kind}", flush=True)

    emit(
        {
            "type": "session_start",
            "serial": serial,
            "device": [dw, dh],
            "canonical": [CANONICAL_WIDTH, CANONICAL_HEIGHT],
            "touch_dev": touch_dev,
        }
    )
    emit({"type": "ui", **snapshot_ui(adb, serial, dw, dh), "why": "start"})

    cmd = [adb, "-s", serial, "shell", "getevent", "-lt"]
    if touch_dev:
        cmd.append(touch_dev)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )

    slot_x = None
    slot_y = None
    last_tap_t = 0.0
    stop_flag = threading.Event()

    def watch_stop():
        while not stop_flag.is_set():
            if stop_path.exists():
                stop_flag.set()
                # getevent bloqueia no readline — forçar saída
                try:
                    proc.terminate()
                except Exception:
                    pass
                break
            time.sleep(0.25)

    # fallback: poll UI em paralelo (nao precisa root; apanha mudancas de ecrã)
    def poll_ui():
        last = ""
        while not stop_flag.is_set():
            ui = snapshot_ui(adb, serial, dw, dh)
            blob = ui.get("blob") or ""
            if blob and blob != last:
                emit({"type": "ui", **ui, "why": "poll_change"})
                last = blob
            time.sleep(0.7)

    threading.Thread(target=poll_ui, daemon=True).start()
    threading.Thread(target=watch_stop, daemon=True).start()

    def emit_tap(px: int, py: int, via: str = "btn") -> None:
        nonlocal last_tap_t
        now = time.time()
        if now - last_tap_t < 0.1:
            return
        last_tap_t = now
        if px > dw * 2 or py > dh * 2:
            # raw max style
            px2 = int(px / 32767 * dw)
            py2 = int(py / 32767 * dh)
            cx, cy = map_physical_to_canonical(px2, py2, dw, dh)
            px, py = px2, py2
        else:
            cx, cy = map_physical_to_canonical(px, py, dw, dh)
        emit({"type": "tap", "x": px, "y": py, "xy_can": [cx, cy], "via": via})
        emit({"type": "ui", **snapshot_ui(adb, serial, dw, dh), "why": "after_tap"})

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if stop_flag.is_set():
                break
            line = line.strip()
            if not line:
                continue
            parsed = _parse_getevent_line(line)
            if not parsed:
                continue
            ev, code, val = parsed
            if "POSITION_X" in code:
                slot_x = val
            elif "POSITION_Y" in code:
                slot_y = val
            elif "BTN_TOUCH" in code and val == 0:
                if slot_x is not None and slot_y is not None:
                    emit_tap(int(slot_x), int(slot_y), "btn")
                slot_x, slot_y = None, None
            elif "SYN_REPORT" in code or (ev == "EV_SYN" and "REPORT" in code):
                if slot_x is not None and slot_y is not None:
                    emit_tap(int(slot_x), int(slot_y), "syn")
                    slot_x, slot_y = None, None
    except KeyboardInterrupt:
        print("TEACH_LOG interrupt", flush=True)
    finally:
        stop_flag.set()
        try:
            proc.terminate()
        except Exception:
            pass
        emit({"type": "session_end", "log": str(log_path.name)})
        latest = OUT_DIR / "events_latest.jsonl"
        try:
            latest.write_bytes(log_path.read_bytes())
        except Exception:
            pass
        print(f"TEACH_LOG saved {log_path}", flush=True)


if __name__ == "__main__":
    main()
