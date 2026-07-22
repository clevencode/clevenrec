"""
Navegador tipo controlo remoto de TV — por aria-label / content-desc.

Modelo mental:
  - Cada elemento interativo é um “canal” com rótulo (text, content-desc, resource-id)
  - `go("Envoyer")` salta direto ao rótulo (como digitar o número do canal)
  - `move("right")` / `move("down")` navega espacialmente entre vizinhos
  - Se o toque falhar ou abrir o ecrã errado → autocorreção (dismiss + retry)
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from .config import CANONICAL_HEIGHT, CANONICAL_WIDTH, settle_for_action
from .executor import ActionExecutor
from .filter import change_ratio
from .normalize import normalize_frame
from .precision import hit_point_for_mark, map_canonical_to_physical
from .som import UiMark, build_som, dump_ui_xml, extract_marks_from_xml

# Diálogos / estados a cancelar automaticamente
_DISMISS_PATTERNS = (
    r"\bannuler\b",
    r"\bcancel\b",
    r"\bcancelar\b",
    r"\bnão\b",
    r"\bno\b",
    r"\bfechar\b",
    r"\bclose\b",
    r"\bdismiss\b",
)

_DANGER_PATTERNS = (
    r"abandonner",
    r"abandon",
    r"discard",
    r"supprimer",
    r"delete",
    r"excluir",
)


@dataclass
class NavResult:
    ok: bool
    mark: Optional[UiMark] = None
    action: str = ""
    message: str = ""
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "action": self.action,
            "message": self.message,
            "mark": None if self.mark is None else self.mark.as_dict(),
            "attempts": self.attempts,
        }


def _norm(s: str) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", s or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.strip().lower())


def aria_blob(mark: UiMark) -> str:
    """Texto pesquisável estilo aria-label (label + cls + extras)."""
    parts = [mark.label, mark.cls]
    extra = getattr(mark, "aria", None) or ""
    if extra:
        parts.append(str(extra))
    return _norm(" ".join(p for p in parts if p))


def match_score(mark: UiMark, needle: str) -> int:
    """
    Score de correspondência aria (estrito).
    Maior = melhor. 0 = sem match.
    Evita falsos positivos tipo label "Message" vs needle "message de statut".
    """
    n = _norm(needle)
    if not n or len(n) < 2:
        return 0
    blob = aria_blob(mark)
    label = _norm(mark.label)
    if not label and not blob:
        return 0

    if label == n or blob == n:
        return 1000
    # phrase completa dentro do label/aria
    if len(n) >= 4 and n in label:
        return 800
    if len(n) >= 4 and n in blob:
        return 700
    # label completo contido no needle — só se label for “quase” o needle
    if len(label) >= 8 and label in n and len(label) >= int(len(n) * 0.55):
        return 550
    # todos os tokens significativos do needle (≥4 chars) presentes
    tokens = [t for t in re.split(r"[\s,;/|_\-]+", n) if len(t) >= 4]
    if len(tokens) >= 2 and all(t in blob for t in tokens):
        return 400 + 15 * len(tokens)
    if len(tokens) == 1 and tokens[0] == label:
        return 450
    if len(tokens) == 1 and tokens[0] in label and len(tokens[0]) >= 5:
        return 350
    return 0


def find_by_aria(
    marks: Sequence[UiMark],
    *needles: str,
    prefer_smaller: bool = True,
    min_score: int = 350,
) -> Optional[UiMark]:
    """Melhor marca cujo aria/label casa com algum needle (ordem = prioridade)."""
    best_m: Optional[UiMark] = None
    best_key: Optional[tuple[int, int, int]] = None
    for pri, needle in enumerate(needles):
        for m in marks:
            sc = match_score(m, needle)
            if sc < min_score:
                continue
            area = m.area if prefer_smaller else -m.area
            key = (pri, -sc, area)
            if best_key is None or key < best_key:
                best_key = key
                best_m = m
    return best_m


def neighbor(
    marks: Sequence[UiMark],
    current: UiMark,
    direction: str,
) -> Optional[UiMark]:
    """
    Vizinho espacial (controlo remoto): up|down|left|right.
    Escolhe o candidato naquele quadrante com menor distância + alinhamento.
    """
    d = (direction or "").strip().lower()
    if d in ("u", "north"):
        d = "up"
    if d in ("b", "south"):
        d = "down"
    if d in ("l", "west"):
        d = "left"
    if d in ("r", "east"):
        d = "right"
    if d not in ("up", "down", "left", "right"):
        raise ValueError(f"direção inválida: {direction}")

    cx, cy = current.cx, current.cy
    best: Optional[tuple[float, UiMark]] = None
    for m in marks:
        if m.id == current.id:
            continue
        dx, dy = m.cx - cx, m.cy - cy
        # precisa estar no semiplano certo com folga mínima
        if d == "up" and dy >= -12:
            continue
        if d == "down" and dy <= 12:
            continue
        if d == "left" and dx >= -12:
            continue
        if d == "right" and dx <= 12:
            continue
        # custo: distância + penalidade de desalinhamento no eixo ortogonal
        if d in ("up", "down"):
            cost = abs(dy) + 1.8 * abs(dx)
        else:
            cost = abs(dx) + 1.8 * abs(dy)
        if best is None or cost < best[0]:
            best = (cost, m)
    return None if best is None else best[1]


class RemoteNavigator:
    """
    Controlo remoto ADB: refresh → go(aria) / move(dir) → tap com autocorreção.
    """

    def __init__(
        self,
        executor: ActionExecutor,
        *,
        serial: str,
        adb_path: str,
        grab_fn: Optional[Callable[[], Any]] = None,
        save_fn: Optional[Callable[[str, Any], Any]] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.ex = executor
        self.serial = serial
        self.adb_path = adb_path
        self.grab_fn = grab_fn
        self.save_fn = save_fn
        self.log = log or (lambda s: print(s, flush=True))
        self.marks: list[UiMark] = []
        self.focus: Optional[UiMark] = None
        self.last_xml: str = ""
        self.last_tag: str = ""

    # --- percepção ---------------------------------------------------------

    def refresh(self, tag: str = "nav") -> list[UiMark]:
        frame = None
        if self.grab_fn:
            frame = self.grab_fn()
        else:
            if self.ex.backend == "scrcpy" and self.ex.scrcpy.connected:
                fr = self.ex.scrcpy.wait_frame(2.0)
                if fr is not None:
                    frame = normalize_frame(fr)
        if frame is None:
            raise RuntimeError("sem frame para refresh do remote")

        annotated, marks, catalog = build_som(
            frame,
            serial=self.serial,
            device_w=self.ex.device_w,
            device_h=self.ex.device_h,
            adb_path=self.adb_path,
        )
        self.last_xml = dump_ui_xml(self.adb_path, self.serial)
        # se SoM filtrou demais, enriquecer com extract completo
        if len(marks) < 3 and self.last_xml:
            marks = extract_marks_from_xml(
                self.last_xml,
                device_w=self.ex.device_w,
                device_h=self.ex.device_h,
            )
        self.marks = marks
        self.ex.set_marks(marks)
        self.last_tag = tag
        if self.save_fn:
            self.save_fn(f"{tag}_raw", frame)
            self.save_fn(f"{tag}_som", annotated)
        self.log(f"[remote:{tag}] marks={len(marks)} focus="
                 f"{None if self.focus is None else self.focus.id}")
        for m in marks[:10]:
            self.log(f"  [{m.id}] {m.label!r} ({m.cx},{m.cy})")
        if len(marks) > 10:
            self.log(f"  ... +{len(marks) - 10}")
        # mantém foco se a marca ainda existir (por label)
        if self.focus is not None:
            kept = find_by_aria(marks, self.focus.label)
            self.focus = kept or (marks[0] if marks else None)
        elif marks:
            self.focus = marks[0]
        return marks

    def labels(self) -> str:
        return " ".join(aria_blob(m) for m in self.marks)

    def has(self, *needles: str) -> bool:
        return find_by_aria(self.marks, *needles) is not None

    def screen_looks_like(self, *needles: str) -> bool:
        blob = self.labels()
        return any(_norm(n) in blob for n in needles)

    # --- acções ------------------------------------------------------------

    def tap(
        self,
        mark: UiMark,
        *,
        why: str = "",
        verify: bool = True,
        long: bool = False,
    ) -> NavResult:
        hx, hy = hit_point_for_mark(mark)
        self.log(
            f"REMOTE {'LONG' if long else 'TAP'} [{mark.id}] {mark.label!r}"
            f" hit=({hx},{hy}) <- {why}"
        )
        before_labels = self.labels()
        before_frame = None
        if verify and self.grab_fn:
            try:
                before_frame = self.grab_fn()
            except Exception:
                before_frame = None

        acao = "long_click" if long else "click"
        result = self.ex.execute(
            {
                "acao": acao,
                "marca": mark.id,
                "coordenadas": {"x": hx, "y": hy},
                "verify": False,
            }
        )
        time.sleep(settle_for_action(acao))
        self.focus = mark
        attempts = [{"exec": result, "mark": mark.id, "why": why}]

        if not verify:
            return NavResult(ok=True, mark=mark, action=acao, attempts=attempts)

        # Verificação primária: fingerprint de aria (mais fiável que pixels)
        self.refresh(tag=f"{self.last_tag or 'tap'}-post")
        after_labels = self.labels()
        label_changed = after_labels != before_labels and bool(after_labels)
        attempts[-1]["label_changed"] = label_changed

        pixel_ratio = 0.0
        if before_frame is not None and self.grab_fn:
            try:
                after_frame = self.grab_fn()
                pixel_ratio = change_ratio(before_frame, after_frame)
                attempts[-1]["ratio"] = round(pixel_ratio, 5)
            except Exception as exc:
                attempts[-1]["verify_err"] = str(exc)

        if label_changed or pixel_ratio >= 0.003:
            return NavResult(ok=True, mark=mark, action=acao, attempts=attempts)

        self.log(
            f"REMOTE miss (labels=same ratio={pixel_ratio:.5f}) — autocorrect"
        )
        return self._autocorrect_tap(mark, why=why, attempts=attempts)

    def _autocorrect_tap(
        self,
        mark: UiMark,
        *,
        why: str,
        attempts: list[dict[str, Any]],
    ) -> NavResult:
        """Recuperação conservadora: re-localiza por aria; offsets só no bounds."""
        from .precision import hit_point_in_bounds

        # 1) re-dump e retap no mesmo aria (sem offsets fora do alvo)
        self.refresh(tag=f"{self.last_tag}-fix")
        again = find_by_aria(self.marks, mark.label, *(mark.aria.split("|") if mark.aria else ()))
        if again is None:
            again = find_by_aria(self.marks, mark.label)
        if again is not None:
            # micro-ajustes SÓ dentro do bounds (estilo D-pad fino)
            biases = [(0.5, 0.5), (0.35, 0.5), (0.65, 0.5), (0.5, 0.35), (0.5, 0.65)]
            before_labels = self.labels()
            for bx, by in biases:
                hx, hy = hit_point_in_bounds(
                    again.x1, again.y1, again.x2, again.y2, bias_x=bx, bias_y=by
                )
                self.log(
                    f"REMOTE bias retry ({bx},{by}) [{again.id}] {again.label!r}"
                )
                self.ex.execute(
                    {
                        "acao": "click",
                        "marca": again.id,
                        "coordenadas": {"x": hx, "y": hy},
                        "verify": False,
                    }
                )
                time.sleep(settle_for_action("click"))
                self.refresh(tag=f"{self.last_tag}-bias")
                attempts.append({"bias": [bx, by], "mark": again.id})
                if self.labels() != before_labels:
                    return NavResult(
                        ok=True,
                        mark=again,
                        action="click_bias",
                        attempts=attempts,
                    )
        return NavResult(
            ok=False,
            mark=mark,
            action="autocorrect_fail",
            message="toque sem efeito após bias no bounds",
            attempts=attempts,
        )

    def go(
        self,
        *aria_labels: str,
        why: str = "",
        expect: Sequence[str] | None = None,
        absent: Sequence[str] | None = None,
        long: bool = False,
        max_tries: int = 3,
        refresh_first: bool = False,
    ) -> NavResult:
        """
        Salta para o elemento pelo aria-label (como canal da TV).
        Se expect/absent forem dados, valida o ecrã pós-toque e autocorige.
        """
        if refresh_first or not self.marks:
            self.refresh(why or "go")

        last = NavResult(ok=False, action="go", message="não encontrado")
        for attempt in range(max_tries):
            self.dismiss_if_needed()
            mark = find_by_aria(self.marks, *aria_labels)
            if mark is None:
                self.log(f"REMOTE GO miss {aria_labels} try={attempt}")
                self.refresh(tag=f"go-miss-{attempt}")
                last = NavResult(
                    ok=False, action="go", message=f"aria não encontrada: {aria_labels}"
                )
                continue

            last = self.tap(mark, why=why or aria_labels[0], long=long, verify=True)
            if expect or absent:
                # tap() já fez refresh; dismiss e revalida
                self.dismiss_if_needed()
                ok_expect = True
                if expect and not any(self.has(e) for e in expect):
                    ok_expect = False
                if absent and any(self.has(a) for a in absent):
                    ok_expect = False
                # Se o ecrã mudou para um estado “sucesso implícito” (sumiu o alvo), ok
                if not ok_expect and not self.has(*aria_labels) and last.ok:
                    # ainda assim exige expect se foi pedido explicitamente
                    self.log(
                        f"REMOTE GO ecrã inesperado após {mark.label!r} — retry"
                    )
                    last = NavResult(
                        ok=False,
                        mark=mark,
                        action="go_unexpected",
                        message="pós-condição falhou",
                    )
                    continue
                if not ok_expect:
                    self.log(
                        f"REMOTE GO ecrã inesperado após {mark.label!r} — retry"
                    )
                    last = NavResult(
                        ok=False,
                        mark=mark,
                        action="go_unexpected",
                        message="pós-condição falhou",
                    )
                    continue
            return last
        return last

    def move(self, direction: str, *, tap: bool = False, why: str = "") -> NavResult:
        """Move o foco espacialmente (setas do comando). Opcionalmente toca."""
        if not self.marks:
            self.refresh("move")
        if self.focus is None and self.marks:
            self.focus = self.marks[0]
        if self.focus is None:
            return NavResult(ok=False, action="move", message="sem foco")
        nxt = neighbor(self.marks, self.focus, direction)
        if nxt is None:
            self.log(f"REMOTE MOVE {direction} — sem vizinho")
            return NavResult(
                ok=False, mark=self.focus, action="move", message="sem vizinho"
            )
        self.focus = nxt
        self.log(
            f"REMOTE FOCUS [{nxt.id}] {nxt.label!r} via {direction}"
        )
        if tap:
            return self.tap(nxt, why=why or f"move:{direction}")
        return NavResult(ok=True, mark=nxt, action=f"focus_{direction}")

    def dismiss_if_needed(self) -> bool:
        """Fecha diálogos perigosos (Abandonner) tocando Annuler/Cancel."""
        danger = any(
            re.search(p, aria_blob(m), re.I) for m in self.marks for p in _DANGER_PATTERNS
        )
        if not danger:
            # ainda assim, se só há diálogo Annuler/Abandonner
            if self.has("abandonner le texte", "discard", "abandonner"):
                danger = True
        if not danger:
            return False
        for m in self.marks:
            lab = aria_blob(m)
            if any(re.search(p, lab, re.I) for p in _DANGER_PATTERNS):
                continue
            if any(re.search(p, lab, re.I) for p in _DISMISS_PATTERNS):
                self.log(f"REMOTE DISMISS [{m.id}] {m.label!r}")
                self.tap(m, why="dismiss", verify=False)
                time.sleep(0.35)
                self.refresh(tag="after-dismiss")
                return True
        return False

    def swipe_until(
        self,
        *target_aria: str,
        row_cy_min: int = 1300,
        row_cy_max: int = 1650,
        max_swipes: int = 6,
        direction: str = "left",  # revela itens a direita
        why: str = "swipe_until",
        step_frac: float = 0.28,
        bidirectional: bool = True,
    ) -> NavResult:
        """
        Desliza uma fila horizontal (cores/fontes) ate achar o aria-alvo.
        Passos curtos (1-2 chips) para nao saltar o alvo; se a fila estabilizar,
        inverte a direcao uma vez (bidirectional).
        """
        dir_now = direction
        flipped = False
        last_fp = ""

        for i in range(max_swipes):
            self.dismiss_if_needed()
            mark = find_by_aria(self.marks, *target_aria)
            if mark is not None and row_cy_min <= mark.cy <= row_cy_max:
                # cores/swatches: verify pixel/label costuma falhar (UI tree igual)
                return self.tap(
                    mark,
                    why=why,
                    verify=not why.startswith("color"),
                )

            row = [
                m
                for m in self.marks
                if row_cy_min < m.cy < row_cy_max and m.area < 80_000
            ]
            row.sort(key=lambda m: m.cx)
            if len(row) < 2:
                self.refresh(tag=f"swipe-empty-{i}")
                self.dismiss_if_needed()
                if find_by_aria(self.marks, *target_aria):
                    continue
                return NavResult(ok=False, action="swipe_until", message="fila vazia")

            fp = "|".join(m.label for m in row)
            if fp == last_fp and bidirectional:
                if not flipped:
                    dir_now = "right" if dir_now == "left" else "left"
                    flipped = True
                    self.log(f"REMOTE SWIPE fila estavel — inverte para {dir_now}")
                else:
                    self.log("REMOTE SWIPE fila estavel nos dois sentidos — para")
                    break
            last_fp = fp

            # passo curto: ~1-2 chips (evita saltar Monte Carlo / Emeraude)
            left, right = row[0], row[-1]
            y = int((left.cy + right.cy) / 2)
            span = max(120, right.cx - left.cx)
            step = max(100, min(280, int(span * max(0.15, min(0.5, step_frac)))))
            mid = (left.cx + right.cx) // 2
            if dir_now == "left":
                x1, x2 = mid + step // 2, mid - step // 2
            else:
                x1, x2 = mid - step // 2, mid + step // 2
            px1, py1 = map_canonical_to_physical(
                x1, y, self.ex.device_w, self.ex.device_h
            )
            px2, py2 = map_canonical_to_physical(
                x2, y, self.ex.device_w, self.ex.device_h
            )
            self.log(
                f"REMOTE SWIPE row {dir_now} step={step} "
                f"({px1},{py1})->({px2},{py2}) i={i} seen={fp[:80]}"
            )
            try:
                if self.ex.backend == "scrcpy" and self.ex.scrcpy.connected:
                    self.ex.scrcpy.swipe(px1, py1, px2, py2, duration_ms=280)
                else:
                    self.ex.adb.swipe(px1, py1, px2, py2, duration_ms=280)
            except Exception as exc:
                self.log(f"REMOTE swipe err {exc}")
            time.sleep(0.45)
            self.refresh(tag=f"swipe-{i}")
            if self.dismiss_if_needed():
                self.log("REMOTE swipe — dismiss, continua com passo curto")
                continue

        mark = find_by_aria(self.marks, *target_aria)
        if mark is not None and row_cy_min <= mark.cy <= row_cy_max:
            return self.tap(
                mark,
                why=why,
                verify=not why.startswith("color"),
            )
        return NavResult(
            ok=False,
            action="swipe_until",
            message=f"alvo nao encontrado: {target_aria}",
        )
    def ensure(
        self,
        *aria_labels: str,
        fallback: Callable[[], None] | None = None,
    ) -> bool:
        """Garante que um elemento está visível; senão tenta fallback + refresh."""
        if self.has(*aria_labels):
            return True
        if fallback:
            fallback()
            time.sleep(0.5)
            self.refresh("ensure")
        return self.has(*aria_labels)
