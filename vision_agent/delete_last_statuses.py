"""Apagar os N status mais recentes (Mes mises à jour)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vision_agent.a11y import A11yNavigator
from vision_agent.config import resolve_adb_path
from vision_agent.executor import ActionExecutor

SERIAL = os.environ.get("VISION_ADB_SERIAL", "192.168.217.222:5555")
ADB = resolve_adb_path()
N = int(os.environ.get("VISION_DELETE_STATUS_N", "6"))


def sh(*a, timeout=40):
    return subprocess.run([ADB, "-s", SERIAL, *a], capture_output=True, timeout=timeout)


def list_option_menus(nav: A11yNavigator):
    """Só 'Plus d'options' da lista — não EXPLORER PLUS / Nouvelle…"""
    opts = []
    for n in nav.nodes:
        if not n.clickable:
            continue
        lab = (n.text or n.content_desc or "").strip().lower()
        if "plus d" not in lab or "option" not in lab:
            continue
        if n.cy <= 180 or n.cy >= 1400:
            continue
        # chip estreito à direita (⋮), não botão full-width
        w = n.bounds[2] - n.bounds[0]
        if w > 160:
            continue
        opts.append(n)
    opts.sort(key=lambda n: n.cy)
    return opts


def ensure_my_status_list(nav: A11yNavigator) -> None:
    sh(
        "shell",
        "am",
        "start",
        "-a",
        "android.intent.action.VIEW",
        "-d",
        "whatsapp://status",
        "com.whatsapp",
    )
    time.sleep(1.2)
    nav.refresh()
    if list_option_menus(nav):
        return
    # abrir lista «Mes mises à jour»
    if nav.has("mes mises"):
        nav.click("Mes mises à jour de statut", "mes mises à jour")
        time.sleep(1.1)
        nav.refresh()
    if not list_option_menus(nav) and nav.has("mon statut"):
        # tap na zona Mon statut / Mes mises
        nav.click("Mon statut", "mes mises")
        time.sleep(1.0)
        nav.refresh()


def confirm_delete(nav: A11yNavigator) -> bool:
    nav.refresh()
    conf = [
        n
        for n in nav.nodes
        if n.clickable and (n.text or "").strip().lower() == "supprimer"
    ]
    if conf:
        conf.sort(key=lambda n: n.cx)
        nav._tap_node(conf[-1], why="confirm-delete")
        return True
    if nav.has("supprimer"):
        # evitar Annuler: preferir nó com cy alto / texto exacto
        for n in nav.nodes:
            lab = (n.text or "").strip().lower()
            if lab == "supprimer" and n.cy > 800:
                nav._tap_node(n, why="confirm-delete")
                return True
        nav.click("Supprimer")
        return True
    return False


def main() -> int:
    print(f"DELETE_STATUS n={N} serial={SERIAL}", flush=True)
    ex = ActionExecutor(serial=SERIAL)
    nav = A11yNavigator(serial=SERIAL, adb_path=ADB, executor=ex)
    ensure_my_status_list(nav)

    deleted = 0
    for i in range(N):
        nav.refresh()
        opts = list_option_menus(nav)
        print(f"--- {i + 1}/{N} menus={len(opts)} ---", flush=True)
        if not opts:
            print("STOP sem mais status", flush=True)
            break

        nav._tap_node(opts[0], why=f"menu-{i}")
        time.sleep(0.55)
        nav.refresh()
        if not nav.has("supprimer"):
            print("WARN menu sem Supprimer — BACK", flush=True)
            nav.back()
            time.sleep(0.4)
            continue

        nav.click("Supprimer")
        time.sleep(0.55)
        if not confirm_delete(nav):
            print("WARN confirm falhou", flush=True)
            nav.back()
            time.sleep(0.3)
            continue
        time.sleep(0.85)
        deleted += 1
        print(f"DELETED {deleted}", flush=True)

    nav.refresh()
    left = len(list_option_menus(nav))
    print(f"DONE deleted={deleted}/{N} remaining≈{left}", flush=True)
    return 0 if deleted == N else (0 if deleted > 0 else 1)


if __name__ == "__main__":
    raise SystemExit(main())
