"""
Loop principal — Bloco 1: captura → normaliza → filtro → salva JPEG se mudou.

Uso (na raiz do repo):
  python -m vision_agent.loop
  python -m vision_agent.loop --show
  python -m vision_agent.loop --serial LMK410HMYP8HSWCIUO
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2

from .capture import AdbFrameSource
from .config import (
    ADB_SERIAL,
    CAPTURE_INTERVAL_S,
    CHANGE_THRESHOLD,
    FRAMES_DIR,
    JPEG_QUALITY,
    resolve_adb_path,
)
from .filter import StabilityFilter
from .normalize import normalize_frame


def save_jpeg(frame, directory: Path, quality: int = JPEG_QUALITY) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")[:-3]
    path = directory / f"frame_{stamp}.jpg"
    ok = cv2.imwrite(
        str(path),
        frame,
        [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)],
    )
    if not ok:
        raise RuntimeError(f"Falha ao gravar JPEG: {path}")
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Vision agent Bloco 1 — captura ADB + filtro de mudança"
    )
    p.add_argument(
        "--serial",
        default=ADB_SERIAL,
        help="Serial ADB (USB ou IP:5555). Padrão: primeiro device online.",
    )
    p.add_argument(
        "--adb",
        default=None,
        help="Caminho do adb.exe (padrão: auto-detect).",
    )
    p.add_argument(
        "--threshold",
        type=float,
        default=CHANGE_THRESHOLD,
        help="Fração mínima de pixels alterados para aprovar (padrão 0.02).",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=CAPTURE_INTERVAL_S,
        help="Segundos entre capturas.",
    )
    p.add_argument(
        "--frames-dir",
        type=Path,
        default=FRAMES_DIR,
        help="Pasta para JPEGs aprovados.",
    )
    p.add_argument(
        "--show",
        action="store_true",
        help="Mostra preview OpenCV do último frame aprovado.",
    )
    p.add_argument(
        "--max-frames",
        type=int,
        default=0,
        help="Para após N frames aprovados (0 = infinito).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    adb_path = args.adb or resolve_adb_path()
    source = AdbFrameSource(adb_path=adb_path, serial=args.serial)
    filt = StabilityFilter(threshold=args.threshold)

    serial = source.ensure_serial()
    print(
        json.dumps(
            {
                "status": "loop_start",
                "adb": adb_path,
                "serial": serial,
                "threshold": args.threshold,
                "frames_dir": str(args.frames_dir),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    approved_count = 0
    skipped = 0

    try:
        while True:
            try:
                raw = source.grab()
                frame = normalize_frame(raw)
                ok, ratio = filt.should_approve(frame)
            except Exception as exc:
                print(
                    json.dumps(
                        {"status": "erro_captura", "message": str(exc)},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                time.sleep(max(args.interval, 0.5))
                continue

            if not ok:
                skipped += 1
                if skipped % 20 == 0:
                    print(
                        json.dumps(
                            {
                                "status": "frame_ignorado",
                                "ratio": round(ratio, 5),
                                "skipped": skipped,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
            else:
                path = save_jpeg(frame, args.frames_dir)
                approved_count += 1
                print(
                    json.dumps(
                        {
                            "status": "frame_aprovado",
                            "path": str(path),
                            "ratio": round(ratio, 5),
                            "approved": approved_count,
                            "shape": list(frame.shape),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                # Stub Bloco 3 — ponto de extensão (API multimodal)
                print(
                    json.dumps(
                        {
                            "status": "stub_analise",
                            "message": "Bloco 3 (IA) ainda não implementado",
                            "path": str(path),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                if args.show:
                    cv2.imshow("vision_agent", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
                if args.max_frames and approved_count >= args.max_frames:
                    break

            time.sleep(max(args.interval, 0.05))
    except KeyboardInterrupt:
        print(
            json.dumps(
                {
                    "status": "loop_stop",
                    "approved": approved_count,
                    "skipped": skipped,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    finally:
        if args.show:
            cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    sys.exit(main())
