"""Cache de visão do ambiente (L1) → predicção de atalho + antecedência.

Contrato alinhado à skill `ambiente-atalho` e a `docs/BASE_NAVEGACAO.md`:

  observar → score(env) → intent (Éléphant/Sans Bold) → anticipate_pick → atalho

Negócio no JSON (`intent`, `checkpoints`, `transitions`, `steps.*.hit`).
Antecedência: burst scroll na direcção conhecida; não analisar opções indesejadas.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from vision_agent.remote import RemoteNavigator, _norm, find_by_aria

# --- constantes de negócio (padronizadas) ---

GOAL_PRIORITY: tuple[str, ...] = (
    "send",
    "font",
    "color",
    "palette",
    "edit",
    "pencil",
    "termine",
    "font_aa",
)

SCORE_ALTA = 0.7
SCORE_MEDIA = 0.5
BONUS_LAST_LABEL = 0.15
PENALTY_CONFLICT = 0.25

# Intenção default = comportamento do utilizador ao postar status
DEFAULT_INTENT: dict[str, Any] = {
    "color": "Éléphant",
    "font": "Sans Bold",
    "skip_other_styles": True,
    "anticipation": {
        "color": {
            "direction": "left",
            "scroll_bursts": 5,
            "step_px": 300,
            "row_cy": [1400, 1600],
            "land_markers": ["scorpion", "rose brûlé", "éléphant", "elephant"],
        },
        "font": {
            "direction": "left",
            "scroll_bursts": 2,
            "step_px": 260,
            "row_cy": [550, 1650],
            "land_markers": ["sans bold"],
        },
    },
}

DEFAULT_TRANSITIONS: dict[str, tuple[str, str]] = {
    "yv_verse": ("clipboard", "clipboard"),
    "status_tab": ("pencil", "deeplink_status+pencil"),
    "composer": ("edit", "paste_then_style"),
    "composer_ready": ("send", "aria_hit"),
    "color_panel": ("color", "anticipate_intent"),
    "font_panel": ("font", "anticipate_intent"),
    "unknown": ("ensure_composer", "recover"),
}

ENV_STEP_HINTS: dict[str, str] = {
    "composer": "edit",
    "composer_ready": "send",
    "status_tab": "pencil",
    "color_panel": "color",
    "font_panel": "font",
    "yv_verse": "verse_long",
}

ENV_TRAIL_ANCHORS: dict[str, tuple[str, ...]] = {
    "yv_verse": ("verse_long", "verse-long"),
    "status_tab": ("pencil", "pencil-resume", "status-tab"),
    "composer": ("edit", "paste", "palette"),
    "composer_ready": ("termine", "font_aa", "send", "palette"),
    "color_panel": ("color", "palette", "termine-color"),
    "font_panel": ("font", "font_aa", "termine-font"),
}

DEFAULT_EXTRA_FINGERPRINTS: dict[str, list[str]] = {
    "yv_verse": ["quant à toi", "paroles cach", "navigate back", "s21"],
    "composer_ready": ["envoyer", "couleur de fond", "sans serif", "sans bold"],
}


@dataclass(frozen=True)
class Prediction:
    env: str
    score: float
    step: str
    shortcut: str
    confidence: str  # alta | media | baixa
    ranked: tuple[tuple[str, float], ...]
    hist_next: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ranked"] = list(self.ranked)
        return d

    def should_shortcut(self) -> bool:
        return self.confidence in ("alta", "media")


@dataclass(frozen=True)
class StyleIntent:
    color: str
    font: str
    skip_other_styles: bool
    anticipation: dict[str, Any]

    def aria_for(self, kind: str) -> tuple[str, ...]:
        if kind == "color":
            base = self.color
            return tuple(
                dict.fromkeys(
                    [
                        base,
                        base.lower(),
                        "éléphant" if "elephant" in _norm(base) else base,
                        "elephant",
                    ]
                )
            )
        if kind == "font":
            base = self.font
            # exactos apenas — nunca aceitar tipografia "qualquer Bold"
            out = [base, base.lower(), "sans bold"]
            if _norm(base) == "sans bold":
                return tuple(dict.fromkeys(out))
            return tuple(dict.fromkeys(out + [_norm(base)]))
        return ()


def blob_norm(labels: str) -> str:
    return _norm(labels or "")


def load_cache(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_cache(path: Path, cache: dict) -> None:
    path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def get_intent(cache: dict) -> StyleIntent:
    """Lê intenção de estilo do cache; default = Éléphant + Sans Bold."""
    raw = dict(DEFAULT_INTENT)
    user = cache.get("intent") or {}
    if isinstance(user, dict):
        raw.update({k: v for k, v in user.items() if k != "anticipation"})
        ant = dict(DEFAULT_INTENT["anticipation"])
        uant = user.get("anticipation") or {}
        if isinstance(uant, dict):
            for k, v in uant.items():
                if isinstance(v, dict):
                    ant[k] = {**(ant.get(k) or {}), **v}
                else:
                    ant[k] = v
        raw["anticipation"] = ant
    steps = cache.get("steps") or {}
    color_aria = (steps.get("color") or {}).get("aria") or []
    font_aria = (steps.get("font") or {}).get("aria") or []
    color = str(raw.get("color") or (color_aria[0] if color_aria else "Éléphant"))
    font = str(raw.get("font") or (font_aria[0] if font_aria else "Sans Bold"))
    return StyleIntent(
        color=color,
        font=font,
        skip_other_styles=bool(raw.get("skip_other_styles", True)),
        anticipation=dict(raw.get("anticipation") or {}),
    )


def label_matches_intent(label: str, intent_names: Sequence[str]) -> bool:
    ln = _norm(label)
    names = [_norm(n) for n in intent_names if n]
    return any(ln == n for n in names)


def _fingerprints(cache: dict) -> dict[str, list[str]]:
    fps = dict(cache.get("checkpoints") or {})
    for name, needles in DEFAULT_EXTRA_FINGERPRINTS.items():
        fps.setdefault(name, list(needles))
    return fps


def _transitions(cache: dict) -> dict[str, tuple[str, str]]:
    out = dict(DEFAULT_TRANSITIONS)
    raw = cache.get("transitions") or {}
    for env, spec in raw.items():
        if isinstance(spec, dict) and spec.get("next"):
            out[env] = (str(spec["next"]), str(spec.get("shortcut") or "aria_hit"))
        elif isinstance(spec, (list, tuple)) and len(spec) >= 1:
            out[env] = (str(spec[0]), str(spec[1] if len(spec) > 1 else "aria_hit"))
    return out


def score_environments(labels: str, cache: dict) -> list[tuple[str, float]]:
    blob = blob_norm(labels)
    steps = cache.get("steps") or {}
    scored: list[tuple[str, float]] = []

    for name, needles in _fingerprints(cache).items():
        if not needles:
            continue
        hit_n = sum(1 for n in needles if _norm(n) in blob)
        score = hit_n / max(len(needles), 1)
        step_key = ENV_STEP_HINTS.get(name)
        if step_key:
            ll = (steps.get(step_key) or {}).get("last_label") or ""
            if ll and _norm(ll)[:24] in blob:
                score += BONUS_LAST_LABEL
        scored.append((name, score))

    colorish = any(
        k in blob for k in ("elephant", "scorpion", "glycine", "soleil", "rose brule")
    )
    fontish = any(
        k in blob for k in ("sans bold", "calistoga", "serif", "morning", "courier")
    )
    out: list[tuple[str, float]] = []
    for name, score in scored:
        s = score
        if name == "color_panel" and fontish and not colorish:
            s -= PENALTY_CONFLICT
        if name == "font_panel" and colorish and not fontish:
            s -= PENALTY_CONFLICT
        if name == "composer" and ("envoyer" in blob and "ecrivez" not in blob):
            s -= 0.1
        out.append((name, round(s, 3)))
    out.sort(key=lambda x: -x[1])
    return out


def next_from_history(cache: dict, env: str) -> Optional[str]:
    anchors = ENV_TRAIL_ANCHORS.get(env) or (env,)
    nxt: Counter[str] = Counter()
    for run in reversed(cache.get("history") or []):
        if not run.get("ok"):
            continue
        trail = run.get("trail") or []
        whys = [str(e.get("why") or "") for e in trail]
        for i, w in enumerate(whys[:-1]):
            if any(a in w for a in anchors):
                nxt[whys[i + 1]] += 1
        if nxt:
            break
    if not nxt:
        return None
    ranked = nxt.most_common()
    for goal in GOAL_PRIORITY:
        for w, _c in ranked:
            if goal in w:
                return w
    return ranked[0][0]


def _promote_composer_ready(
    env: str, score: float, ranked: list[tuple[str, float]], blob: str
) -> tuple[str, float]:
    if "envoyer" not in blob:
        return env, score
    if any(k in blob for k in ("elephant", "scorpion", "termine")) and env in (
        "color_panel",
        "font_panel",
    ):
        return env, score
    if score < SCORE_ALTA or env == "composer":
        for name, sc in ranked:
            if name == "composer_ready" and sc >= SCORE_MEDIA:
                return name, max(sc, SCORE_ALTA)
        if "ecrivez" not in blob:
            return "composer_ready", max(score, 0.65)
    return env, score


def confidence_for(
    score: float, step: str, cache: dict, *, recent_ok: bool
) -> str:
    steps = cache.get("steps") or {}
    if score >= SCORE_ALTA and recent_ok:
        return "alta"
    if score >= SCORE_MEDIA or (steps.get(step) or {}).get("hit"):
        return "media"
    return "baixa"


def predict_next(labels: str, cache: dict) -> Prediction:
    ranked = score_environments(labels, cache)
    env, score = ranked[0] if ranked else ("unknown", 0.0)
    blob = blob_norm(labels)
    env, score = _promote_composer_ready(env, score, ranked, blob)

    transitions = _transitions(cache)
    step, shortcut = transitions.get(env, transitions["unknown"])
    hist_next = next_from_history(cache, env)

    if hist_next:
        hist_step = None
        for key in GOAL_PRIORITY:
            if key in hist_next:
                hist_step = key
                break
        if env == "unknown" and hist_step:
            step = hist_step
        elif hist_step == step:
            score = min(1.25, round(score + 0.05, 3))

    recent_ok = any(r.get("ok") for r in (cache.get("history") or [])[-3:])
    conf = confidence_for(score, step, cache, recent_ok=recent_ok)
    return Prediction(
        env=env,
        score=score,
        step=step,
        shortcut=shortcut,
        confidence=conf,
        ranked=tuple(ranked[:4]),
        hist_next=hist_next,
    )


def xy_of(entry: dict) -> Optional[list[int]]:
    for key in ("hit", "xy"):
        v = entry.get(key)
        if v and len(v) >= 2:
            return [int(v[0]), int(v[1])]
    return None


def record_hit(cache: dict, key: str, hit: list[int], label: str = "") -> None:
    entry = cache.setdefault("steps", {}).setdefault(key, {})
    entry["hit"] = list(hit)
    if label:
        entry["last_label"] = label


def _row_marks(nav: RemoteNavigator, cy_min: int, cy_max: int) -> list:
    row = [m for m in nav.marks if cy_min < m.cy < cy_max and m.area < 80_000]
    row.sort(key=lambda m: m.cx)
    return row


def ruler_from_row(
    nav: RemoteNavigator,
    cy_min: int,
    cy_max: int,
    *,
    default_step: int = 300,
) -> dict[str, int]:
    """Régua tátil sob medida: pitch mediano dos chips → step_px preciso."""
    row = _row_marks(nav, cy_min, cy_max)
    gaps = [
        row[i + 1].cx - row[i].cx
        for i in range(len(row) - 1)
        if row[i + 1].cx - row[i].cx >= 60
    ]
    if len(gaps) >= 2:
        gaps.sort()
        pitch = gaps[len(gaps) // 2]
    elif gaps:
        pitch = gaps[0]
    else:
        pitch = 0
    if pitch >= 80:
        # ~1.65 chips por gesto — chega ao fim sem Abandonner
        step = int(min(360, max(200, round(pitch * 1.65))))
    else:
        step = int(default_step)
    y = int((row[0].cy + row[-1].cy) / 2) if row else (cy_min + cy_max) // 2
    return {"pitch": pitch, "step_px": step, "n": len(row), "y": y}


def _find_intent_in_row(
    nav: RemoteNavigator, names: Sequence[str], cy_min: int, cy_max: int
):
    names_n = [_norm(n) for n in names if n]
    for m in _row_marks(nav, cy_min, cy_max):
        if label_matches_intent(m.label, names_n):
            return m
    m = find_by_aria(nav.marks, *names)
    if m and cy_min < m.cy < cy_max and label_matches_intent(m.label, names_n):
        return m
    return None


def _swipe_row(
    nav: RemoteNavigator,
    *,
    direction: str,
    step_px: int,
    cy_min: int,
    cy_max: int,
    duration_ms: int = 200,
) -> None:
    from vision_agent.precision import map_canonical_to_physical

    row = _row_marks(nav, cy_min, cy_max)
    if len(row) >= 2:
        y = int((row[0].cy + row[-1].cy) / 2)
        mid = (row[0].cx + row[-1].cx) // 2
    else:
        y = (cy_min + cy_max) // 2
        mid = 540
    if direction == "left":
        x1, x2 = mid + step_px // 2, mid - step_px // 2
    else:
        x1, x2 = mid - step_px // 2, mid + step_px // 2
    px1, py1 = map_canonical_to_physical(x1, y, nav.ex.device_w, nav.ex.device_h)
    px2, py2 = map_canonical_to_physical(x2, y, nav.ex.device_w, nav.ex.device_h)
    try:
        if nav.ex.backend == "scrcpy" and nav.ex.scrcpy.connected:
            nav.ex.scrcpy.swipe(px1, py1, px2, py2, duration_ms=duration_ms)
        else:
            nav.ex.adb.swipe(px1, py1, px2, py2, duration_ms=duration_ms)
    except Exception as exc:
        print(f"ANTECIP swipe err {exc}", flush=True)


def anticipate_pick(
    nav: RemoteNavigator,
    cache: dict,
    *,
    kind: str,
    scroll_sleep: float = 0.14,
    why: str = "",
) -> Optional[Any]:
    """Antecedência: burst até ao alvo da intenção; ignora opções indesejadas."""
    intent = get_intent(cache)
    ant = (intent.anticipation or {}).get(kind) or {}
    names = intent.aria_for(kind)
    row_cy = ant.get("row_cy") or [1400, 1600]
    cy_min, cy_max = int(row_cy[0]), int(row_cy[1])
    direction = str(ant.get("direction") or "left")
    bursts = int(ant.get("scroll_bursts") or 3)
    step_default = int(ant.get("step_px") or 300)
    why = why or f"anticipate-{kind}"

    # Guard: painel errado (overlay agenda, etc.) — não burst cego nem hit fantasma
    blob = blob_norm(nav.labels())
    if kind == "color":
        panel_ok = any(
            k in blob
            for k in ("termine", "elephant", "scorpion", "soleil", "monte carlo", "glycine")
        )
        if not panel_ok:
            print("ANTECIP color abort: nao e color_panel", flush=True)
            return None
    if kind == "font":
        panel_ok = any(
            k in blob for k in ("sans bold", "serif", "calistoga", "morning", "exo")
        )
        if not panel_ok:
            print("ANTECIP font abort: nao e font_panel", flush=True)
            return None

    # tab-first: alvo já nos elementos → tap (skill navegacao-tab)
    hit = _find_intent_in_row(nav, names, cy_min, cy_max)
    if hit is not None:
        print(f"ANTECIP {kind} tab {hit.label!r} — tap", flush=True)
        nav.tap(hit, why=why, verify=False)
        return hit

    for m in nav.marks:
        if label_matches_intent(m.label, names):
            print(f"ANTECIP {kind} tab global {m.label!r} cy={m.cy} — tap", flush=True)
            nav.tap(m, why=why, verify=False)
            return m

    # régua tátil: recalibrar step a cada burst
    ruler = ruler_from_row(nav, cy_min, cy_max, default_step=step_default)
    step_px = ruler["step_px"]
    print(
        f"REGUA {kind} pitch={ruler['pitch']} step={step_px} n={ruler['n']}",
        flush=True,
    )

    for i in range(bursts):
        ruler = ruler_from_row(nav, cy_min, cy_max, default_step=step_default)
        step_px = ruler["step_px"]
        # perto do fim (land) → passo mais curto = mais precisão
        row_blob = " ".join(_norm(m.label) for m in _row_marks(nav, cy_min, cy_max))
        near_end = any(
            k in row_blob
            for k in ("glycine", "etoile", "elephant", "scorpion", "sans bold", "calistoga")
        )
        if near_end:
            step_px = max(180, int(step_px * 0.75))
        print(
            f"ANTECIP {kind} burst {i + 1}/{bursts} dir={direction} "
            f"step={step_px} alvo={names[0]!r}",
            flush=True,
        )
        _swipe_row(
            nav,
            direction=direction,
            step_px=step_px,
            cy_min=cy_min,
            cy_max=cy_max,
            duration_ms=170 if near_end else 200,
        )
        time.sleep(scroll_sleep)
        nav.refresh(f"{why}-b{i}")
        nav.dismiss_if_needed()
        hit = _find_intent_in_row(nav, names, cy_min, cy_max)
        if hit is None:
            for m in nav.marks:
                if label_matches_intent(m.label, names):
                    hit = m
                    break
        if hit is not None:
            # tab: se já em faixa segura, não inset
            if 100 <= hit.cx <= 980:
                print(f"ANTECIP OK {kind} tab {hit.label!r} cx={hit.cx}", flush=True)
                nav.tap(hit, why=why, verify=False)
                return hit
            # fora da faixa → inset curto (anti-Abandonner)
            from vision_agent.precision import map_canonical_to_physical

            y = hit.cy
            target_x = 900 if hit.cx > 980 else 180
            px1, py1 = map_canonical_to_physical(
                hit.cx, y, nav.ex.device_w, nav.ex.device_h
            )
            px2, py2 = map_canonical_to_physical(
                target_x, y, nav.ex.device_w, nav.ex.device_h
            )
            print(
                f"ANTECIP inset {hit.label!r} {hit.cx}->{target_x}",
                flush=True,
            )
            try:
                if nav.ex.backend == "scrcpy" and nav.ex.scrcpy.connected:
                    nav.ex.scrcpy.swipe(px1, py1, px2, py2, duration_ms=160)
                else:
                    nav.ex.adb.swipe(px1, py1, px2, py2, duration_ms=160)
            except Exception:
                pass
            time.sleep(scroll_sleep)
            nav.refresh(f"{why}-inset")
            nav.dismiss_if_needed()
            again = _find_intent_in_row(nav, names, cy_min, cy_max)
            if again is None:
                for m in nav.marks:
                    if label_matches_intent(m.label, names):
                        again = m
                        break
            if again is not None:
                hit = again
            else:
                if nav.has("abandonner", "annuler"):
                    nav.dismiss_if_needed()
                    again = _find_intent_in_row(nav, names, cy_min, cy_max)
                    if again is None:
                        for m in nav.marks:
                            if label_matches_intent(m.label, names):
                                again = m
                                break
                    if again is not None:
                        hit = again
                    else:
                        print(
                            f"ANTECIP {kind} perdido apos inset/dismiss",
                            flush=True,
                        )
                        continue
                else:
                    print(
                        f"ANTECIP {kind} alvo sumiu apos inset — retap burst",
                        flush=True,
                    )
                    continue
            live = any(label_matches_intent(m.label, names) for m in nav.marks)
            if not live:
                print(f"ANTECIP {kind} marca stale — continua", flush=True)
                continue
            if not label_matches_intent(hit.label, names):
                for m in nav.marks:
                    if label_matches_intent(m.label, names):
                        hit = m
                        break
            print(f"ANTECIP OK {kind} {hit.label!r}", flush=True)
            nav.tap(hit, why=why, verify=False)
            return hit

    # hit de cache só se o painel ainda for o certo e last_label = intent
    step = (cache.get("steps") or {}).get(kind) or {}
    ll = step.get("last_label") or ""
    blob2 = blob_norm(nav.labels())
    panel_still = (
        (kind == "color" and any(k in blob2 for k in ("termine", "scorpion", "soleil")))
        or (kind == "font" and any(k in blob2 for k in ("serif", "calistoga", "termine")))
    )
    if panel_still and ll and label_matches_intent(ll, names):
        xy = xy_of(step)
        # nao tap hit fora da faixa segura (fantasma tipo x=1058)
        if xy and 80 <= int(xy[0]) <= 1000:
            print(
                f"ANTECIP {kind} fallback hit {xy} ({ll})",
                flush=True,
            )
            nav.ex.execute(
                {
                    "acao": "click",
                    "coordenadas": {"x": int(xy[0]), "y": int(xy[1])},
                    "verify": False,
                }
            )
            return step
        print(f"ANTECIP {kind} skip hit fora de faixa {xy}", flush=True)

    print(f"ANTECIP MISS {kind} alvo={names}", flush=True)
    return None


class PosCache:
    """Runtime: predicção + toque aria→hit→xy + retry do último ponto bom."""

    def __init__(
        self,
        cache: dict,
        nav: RemoteNavigator,
        ex: Any,
        *,
        cache_path: Path,
        serial: str = "",
        reopen_composer: Optional[Callable[[], bool]] = None,
    ):
        self.cache = cache
        self.st = cache.setdefault("steps", {})
        self.t = cache.get("timing") or {}
        self.cps = cache.get("checkpoints") or {}
        self.nav = nav
        self.ex = ex
        self.cache_path = cache_path
        self.serial = serial
        self._reopen_composer = reopen_composer
        self.last_good: Optional[str] = None
        self.hits: dict[str, dict] = {}
        self.trail: list[dict] = []
        self.last_pred: Optional[Prediction] = None
        self.intent = get_intent(cache)

    def predict(self, tag: str = "") -> Prediction:
        pred = predict_next(self.nav.labels(), self.cache)
        self.last_pred = pred
        print(
            f"PREDICT{(' ' + tag) if tag else ''} env={pred.env} "
            f"score={pred.score} step={pred.step} "
            f"conf={pred.confidence} shortcut={pred.shortcut}"
            + (f" hist→{pred.hist_next}" if pred.hist_next else "")
            + f" intent={self.intent.color}/{self.intent.font}",
            flush=True,
        )
        return pred

    def should_shortcut(self, pred: Optional[Prediction] = None) -> bool:
        p = pred or self.last_pred
        return bool(p and p.should_shortcut())

    def tap_xy(self, xy: list[int], why: str, long: bool = False) -> None:
        print(f"POS {why} {xy}", flush=True)
        self.ex.execute(
            {
                "acao": "long_click" if long else "click",
                "coordenadas": {"x": int(xy[0]), "y": int(xy[1])},
                "verify": False,
            }
        )
        self.trail.append({"why": why, "xy": list(xy), "t": round(time.time(), 2)})

    def tap_step(self, key: str, why: Optional[str] = None) -> bool:
        why = why or key
        entry = self.st.get(key) or {}
        aria = entry.get("aria") or []
        if aria and self.nav.marks:
            m = find_by_aria(self.nav.marks, *aria)
            if m:
                self.nav.tap(m, why=why, verify=False)
                hit = [int(m.cx), int(m.cy)]
                self.record(key, hit, label=m.label)
                self.last_good = key
                return True
        sess = self.hits.get(key) or {}
        xy = sess.get("hit") or xy_of(entry)
        if xy:
            self.tap_xy(xy, why)
            self.last_good = key
            return True
        print(f"MISS {why} sem aria/xy", flush=True)
        return False

    def record(self, key: str, hit: list[int], label: str = "") -> None:
        if key == "color" and label and self.intent.skip_other_styles:
            if not label_matches_intent(label, self.intent.aria_for("color")):
                print(
                    f"CACHE skip record color {label!r} != intent {self.intent.color}",
                    flush=True,
                )
                self.trail.append(
                    {
                        "why": key,
                        "xy": list(hit),
                        "label": label,
                        "skipped_intent": True,
                        "t": round(time.time(), 2),
                    }
                )
                return
        if key == "font" and label and self.intent.skip_other_styles:
            if not label_matches_intent(label, self.intent.aria_for("font")):
                print(
                    f"CACHE skip record font {label!r} != intent {self.intent.font}",
                    flush=True,
                )
                self.trail.append(
                    {
                        "why": key,
                        "xy": list(hit),
                        "label": label,
                        "skipped_intent": True,
                        "t": round(time.time(), 2),
                    }
                )
                return
        self.hits[key] = {"hit": list(hit), "label": label}
        record_hit(self.cache, key, hit, label)
        self.trail.append(
            {"why": key, "xy": list(hit), "label": label, "t": round(time.time(), 2)}
        )

    def in_checkpoint(self, name: str) -> bool:
        needles = self.cps.get(name) or []
        return bool(needles) and self.nav.has(*needles)

    def ensure_composer(self, max_tries: int = 2) -> bool:
        self.nav.dismiss_if_needed()
        if self.in_checkpoint("composer") or self.nav.has(
            "écrivez", "envoyer", "couleur de fond"
        ):
            return True
        reopen = self._reopen_composer
        if not reopen:
            return False
        for _ in range(max_tries):
            if reopen():
                self.last_good = "composer"
                return True
            time.sleep(float(self.t.get("retry_s", 0.35)))
        return False

    def retry_from_last(
        self,
        target_key: str,
        action: Callable[[], bool],
        *,
        max_tries: int = 3,
        need_composer: bool = True,
    ) -> bool:
        for attempt in range(1, max_tries + 1):
            print(
                f"RETRY {target_key} #{attempt}/{max_tries} last_good={self.last_good}",
                flush=True,
            )
            if need_composer and not self.ensure_composer():
                continue
            try:
                if action():
                    self.last_good = target_key
                    return True
            except Exception as exc:
                print(f"RETRY err {target_key}: {exc}", flush=True)
            time.sleep(float(self.t.get("retry_s", 0.35)))
        return False

    def persist(self, ok: bool, elapsed: float) -> None:
        hist = self.cache.setdefault("history", [])
        hist.append(
            {
                "ok": ok,
                "elapsed_s": elapsed,
                "serial": self.serial,
                "last_good": self.last_good,
                "hits": dict(self.hits),
                "trail": self.trail[-40:],
                "pred": self.last_pred.as_dict() if self.last_pred else None,
                "intent": {"color": self.intent.color, "font": self.intent.font},
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        )
        self.cache["history"] = hist[-12:]
        self.cache.setdefault(
            "intent",
            {
                "color": self.intent.color,
                "font": self.intent.font,
                "skip_other_styles": self.intent.skip_other_styles,
                "anticipation": self.intent.anticipation,
            },
        )
        save_cache(self.cache_path, self.cache)
        print(f"CACHE SAVED hits={list(self.hits)} ok={ok}", flush=True)
