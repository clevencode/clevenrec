---
name: navegacao-tab
description: >-
  Navegação por tab como prioridade; deslizar apenas quando não houver tab nos
  elementos. Usa dados táteis técnicos (bounds, hit, pitch, fila_end) como régua
  sob medida para impulsionar a análise antecipativa. Use em vision_agent, SoM,
  ADB, filas de cor/fonte, bottom nav Status, anticipate_pick, ou quando o
  utilizador pede tab-first, swipe só se necessário, ou régua tátil.
---

# Navegação por tab (prioridade) + régua tátil

**Princípio (verbatim):** navegacao por tab como prioridade, deslizar apenas quando nao houver tab nos elementos: use dados tateis técnicos como régua sob medida para impulsionar a analise antecipativa.

## Ordem de decisão

```
1. OBSERVAR   → SoM / labels / bounds
2. HÁ TAB?    → sim: TOCAR a tab (aria/hit) — não scroll
3. SEM TAB?   → só então DESLIZAR (burst / swipe_until)
4. RÉGUA      → dados táteis calibram quanto e para onde antecipar
5. VERIFICAR  → expect; se miss → skill falha-inesperada
```

**Tab ganha sempre** a swipe. Swipe é fallback, não hábito.

## O que conta como “tab” nos elementos

Qualquer alvo **discretamente tocável** já presente no dump, alinhado à intenção:

| Tipo | Exemplos SoM |
|------|----------------|
| Tab / bottom nav | `Discussions`, `Actus` / Status, `Nouveau message de statut` |
| Chip nomeado na fila | `Éléphant`, `Sans Bold` **já visíveis** |
| Acção de barra | `TERMINÉ`, `Envoyer`, `Couleur de fond`, `Sans Serif` |
| cls típico | `TabWidget`, `Tab`, botão com aria estável |

**Não** é tab: zona vazia da fila, “qualquer cor a seguir”, gesto cego sem label-alvo.

Regra: se `find_by_aria` / intent label está em `nav.marks` → **tap**; proibido burst “por costume”.

## Dados táteis = régua sob medida

A análise antecipativa não inventa metros: mede o que o SoM já deu.

| Dado tátil | Uso na régua | Impulso antecipativo |
|------------|--------------|----------------------|
| `bounds` / área | largura do chip | `step_px` ≈ 1.2–1.8× pitch médio |
| `hit` (inset) | ponto seguro de toque | prior sobre centro geométrico |
| `cx, cy` + `row_cy` | faixa da fila | bursts só nessa régua Y |
| `fila_end` / land_markers | marco do fim | parar quando a régua “chega” |
| `from_right` / índice | posição relativa | atalho quando tab ainda não visível |
| `last_label` + history ok | calibração viva | confiar na direcção/N bursts |
| pitch entre marks vizinhos | distância chip-a-chip | swipe curto (anti-Abandonner) |

```
régua.step_px   = median(|cx[i+1]-cx[i]|) dos chips na row   # sob medida
régua.bursts    ≈ distância_estimada_ao_alvo / step_px
régua.inset     = nudge curto se cx fora de [100, 980]        # não centrar a 540
```

Sem régua tátil → não adivinhar swipe largo; preferir tab/deeplink/`from_right` conhecido.

## Ciclo tab-first (missão Status)

| Situação | Acção |
|----------|--------|
| Éléphant já no dump | **tab** (tap) — 0 bursts |
| Sans Bold já no dump | **tab** (tap) |
| Palette / AA / Envoyer / TERMINÉ visíveis | **tab** |
| Alvo da intenção ausente na fila | régua → N bursts → procurar de novo → **tab** |
| Overlay / sem tabs úteis | skill `falha-inesperada` → voltar; depois tab-first outra vez |

## Implementação canónica

| Peça | Papel |
|------|--------|
| `anticipate_pick` | já-visível = tab; senão burst com `step_px` / `row_cy` do intent |
| `find_by_aria` / `PosCache.tap_step` | tab por aria→hit |
| `pick_color_from_right` / `swipe_until` | só quando não há tab do alvo |
| `steps.*.hit`, `fila_end`, `anticipation.*` | régua no JSON |
| `precision.hit_point_*` | toque tátil seguro |

```python
# padrão tab-first
m = find_intent_in_marks(nav, intent)   # tab?
if m:
    nav.tap(m)                          # prioridade
else:
    step = ruler_from_row(nav.marks)    # régua tátil
    anticipate_pick(..., step_px=step)  # deslizar só agora
```

## Ligação às outras skills

- `ambiente-atalho` — o quê atingir (intent) e quando atalhar
- `falha-inesperada` — overlay sem tabs úteis → regresso
- Esta skill — **como** mover: tab > swipe; swipe medido pela régua

## Anti-padrões

- Burst/scroll com o alvo já listado nos marks
- Swipe full-width / center-to-540 (abre Abandonner) em vez de inset da régua
- `step_px` fixo genérico quando há pitch medível na fila
- Tratar bottom-nav Status como “deslizar até achar”
- Analisar chips indesejados em vez de tab no intent
