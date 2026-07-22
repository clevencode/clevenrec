---
name: navegacao-a11y
description: >-
  Navegação ADB pela árvore de acessibilidade Android (uiautomator), com plano
  visual, rastreio de clique e tab-first. Use com vision_agent.a11y,
  A11yNavigator, VisualPlane, locate_chip_row, scroll_seek, click_verified,
  missões Status/YouVersion, ou quando o utilizador pede a11y, acessibilidade,
  régua de chips, ou teste sem SoM.
---

# Navegação ADB por acessibilidade (A11y-first)

**Princípio:** o sensor primário é a árvore Accessibility (`uiautomator dump`); o toque é ADB/scrcpy nos `bounds` físicos. Frame é opcional — serve para `change_ratio`, limites do plano e debug.

**Negócio (alinhado a `ambiente-atalho`):** intenção fixa Éléphant + Sans Bold; não analisar outras cores/tipografias; antecedência de movimento até ao alvo; tab se o label já estiver no dump.

## Ordem de decisão

```
1. DUMP        → refresh()
2. HÁ TAB?     → find exacto do intent → click / click_verified  (tab-first)
3. PLANO       → locate_chip_row / infer_plane (limites da fila)
4. SEM TAB?    → scroll_seek DENTRO do plano (régua pitch); CV corrige direcção
5. FALHA?      → dismiss leve / pré-visual → relocalizar plano → retomar passo
6. VERIFICAR   → expect/absent + change_ratio; overlay → skill falha-inesperada
```

**Tab ganha sempre** a swipe (`navegacao-tab`). Swipe só com objectivo ausente.

## API canónica

| Método | Uso |
|--------|-----|
| `refresh()` | dump + flatten |
| `click` / `click_verified` | tab no nó; verify visual + próximo candidato |
| `infer_plane` / `locate_chip_row` | limites do plano (font/color/noise) |
| `scroll(..., plane=)` | swipe **dentro** dos bounds do plano |
| `scroll_seek` | antecede até ao intent; inverte se band_ratio≈0 |
| `has_all` | AND estrito (evitar «Envoyer un message à l'IA») |
| `visual_hits` | rastreio hit_can / in_plane / bias |
| CLI | `python -u -m vision_agent.a11y --dump\|--click\|--scroll` |

## Plano visual + rastreio de clique

| Conceito | Regra |
|----------|--------|
| `VisualPlane` | união dos chips clicáveis na faixa Y; `kind` ∈ font\|color\|noise\|unknown |
| `locate_chip_row` | fila pode estar a meio (~526) **ou** fundo (~1248) — nunca hardcodar só um |
| Swipe | `sx,sy` clamados aos limites do plano (anti-texto / anti-Abandonner) |
| Pitch | `step` ≈ mediana \|cx[i+1]-cx[i]\| → `amount` sob medida |
| Ruído | pré-visual `bible.com` / `Aperçu` → dismiss, **não** scroll cego |
| Debug | `.screenshots/a11y_plane/*` — retângulo + seta + hits |

```
régua.pitch   = median(|cx[i+1]-cx[i]|) dos chips no plano
régua.amount  = clamp(2.4–2.9 * pitch / plane.width, 0.36, 0.62)  # treino: 1.4 era curto
régua.at_cy   = plane.cy
régua.burst   = ×3 se paleta no início (ciel/maya) — Éléphant no fim
```

## Missão Status (intent fixo)

1. Deeplink YV `DAN.12.4.S21` → long_click / clipboard  
2. `whatsapp://status` → «Nouveau message de statut»  
3. EditText → paste → BACK (teclado)  
4. Dismiss «Supprimer l'aperçu du lien» se existir  
5. «Couleur de fond» → **tab Éléphant** ou `scroll_seek` + burst → TERMINÉ  
6. Toolbar «Sans Serif» (`cy_max≈250`) → expect chips (`Morning Breeze`/`Calistoga`…)  
7. **Tab Sans Bold** ou 1× scroll left → TERMINÉ  
8. **Envoyer** → «Mes mises» / «Vu par»  

Intent / treino: `step_cache_yv_status_a11y.json` + doc `docs/TREINO_A11Y_STATUS.md`.

```bash
python -u -m vision_agent.yv_status_a11y_test
python -u -m vision_agent.yv_status_a11y_test --auto --rounds 5
python -u -m vision_agent.delete_last_statuses   # N=6 via VISION_DELETE_STATUS_N
```

## Relação com outras skills

| Skill | Papel |
|-------|--------|
| `navegacao-tab` | tab > swipe; régua tátil → aqui = plano + pitch |
| `ambiente-atalho` | intent Éléphant/Sans Bold + antecedência |
| `falha-inesperada` | plano noise / overlay → dismiss → composer → retomar |

## Anti-padrões

- Expect «serif»/«bold» sozinho (falso positivo no botão toolbar)
- `has("envoyer", "couleur")` como OR acidental na lista de chats
- Scroll na faixa Y errada (texto do verso em vez da fila de chips)
- Analisar Calistoga/Pourpre como candidatos quando o intent é Sans Bold/Éléphant
- Swipe full-width fora do plano
- Reiniciar a missão por pré-visual de link
