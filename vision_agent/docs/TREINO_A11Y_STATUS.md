# Treino A11y Status — relatório e cache de visão

**Missão:** YouVersion Daniel 12:4 → WhatsApp Status (Éléphant + Sans Bold → Envoyer)  
**Device:** 720×1600 · `VISION_ADB_SERIAL` (ex. `192.168.217.222:5555`)  
**Cache:** `vision_agent/step_cache_yv_status_a11y.json`  
**Skills:** `navegacao-a11y`, `navegacao-tab`, `ambiente-atalho`, `falha-inesperada`

## Objectivo do treino

1. Validar navegação **A11y-first** (uiautomator) com plano visual e rastreio de clique.  
2. Gravar **cache de visão** a cada iteração (hits, `cy_band`, direcção, anticipation).  
3. Acelerar com shrink de `timing` e antecedência (burst / pitch).  
4. Refinar comportamento a partir do relatório dos rounds.

## Como repetir

```bash
set PYTHONIOENCODING=utf-8
set VISION_ADB_SERIAL=<device>

# uma corrida (grava cache)
python -u -m vision_agent.yv_status_a11y_test

# até 5 iterações: save cache + shrink timing
python -u -m vision_agent.yv_status_a11y_test --auto --rounds 5 --target 85

# apagar N status mais recentes (Mes mises → ⋮ → Supprimer)
set VISION_DELETE_STATUS_N=6
python -u -m vision_agent.delete_last_statuses
```

## Rounds auto (treino 2026-07-22)

| Round | Tempo | Cor | Resultado |
|------:|------:|-----|----------|
| 1 | 135.7s | 5 scrolls (ciel/maya) | OK |
| 2 | 147.4s | 5 scrolls (ciel/maya) | OK |
| 3 | **123.7s** | 1 scroll (violine) | OK — melhor do auto |
| 4 | 137.0s | 3 scrolls (monte carlo) | OK |
| 5 | 159.5s | 5 scrolls; `max_scrolls=4` | FAIL cor |

**Smoke pós-refino:** FINAL_OK **102.9s** (burst + amount maior + floor de scrolls).

### Achados visuais

- Fila de **cores/fontes** no fundo (`cy≈1248`) com texto colado; a meio (`~526`) sem pré-visual.  
- **Éléphant** no fim da paleta (após étoile/gris); início = ciel / bleu maya / monte carlo.  
- **Sans Bold** = 1× scroll `left` a partir de Sans Serif (estável).  
- `amount≈0.28` (~1.4 chips) → demasiados seeks; usar ~2.4–2.9 chips + **burst×3** no início.  
- Não baixar `max_scrolls_color` abaixo de **8**.  
- Pré-visual `bible.com` ocupa a faixa — dismiss antes do painel de fontes.  
- Envoyer com `change_ratio` alto: **não** BACK (`falha-inesperada`).

## Refinos codificados

| Peça | Comportamento |
|------|----------------|
| `VisualPlane` / `locate_chip_row` | Limites reais da fila; swipe dentro do plano |
| `swipe_burst` | Antecedência sem dump a cada gesto |
| `scroll_seek(..., burst_n=)` | Detecta paleta no início → burst |
| `amount_from_plane(chips_per_swipe=)` | Cor ≈2.9; font ≈2.2 |
| Cache `vision.*` / `intent.anticipation` | hit_can, cy_band, direction, burst_n |
| `delete_last_statuses` | Só «Plus d'options» (não EXPLORER PLUS) |

## Estrutura do cache A11y

```json
{
  "intent": { "color": "Éléphant", "font": "Sans Bold", "anticipation": { ... } },
  "timing": { "yv_open_s": ..., "after_send_s": ... },
  "vision": { "color": { "hit_can", "cy_band", "burst_n" }, "font": { ... } },
  "history": [ { "round", "ok", "elapsed_s", "timing" } ],
  "visual_report": { "rounds", "findings" },
  "auto_improve": { "best_elapsed_s", "target_s" }
}
```

## Critério de sucesso

- `FINAL_OK` com `color=True font=True send=True published=True`  
- Cache actualizado após cada run / round  
- Intent fixo — sem escolher outras cores/fontes
