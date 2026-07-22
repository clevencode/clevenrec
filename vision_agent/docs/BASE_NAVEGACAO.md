# Base de navegação ADB — documentação para próximos níveis

Documento de referência da pilha `vision_agent` após o fluxo YouVersion → WhatsApp Status
estar estável (**10/10** / **100/10**). Serve de contrato para estender a novos apps e missões.

---

## 1. Objetivo da base

Automatizar qualquer app Android com:

1. **Percepção** — dump UI + frame (scrcpy / screencap)
2. **Identidade** — elementos por `text` / `content-desc` / `resource-id` (estilo aria-label)
3. **Controlo** — toques e swipes no espaço **físico** (`wm size`)
4. **Malha fechada** — settle + verificação + autocorreção
5. **Missões determinísticas** — scripts sem LLM (nível atual)

Nível atual = **piloto automático por labels**.  
Próximos níveis podem acrescentar LLM, planeador, memória de ecrãs, etc., **sem reescrever** a camada de toque.

---

## 2. Arquitetura (camadas)

```
┌─────────────────────────────────────────────────────────────┐
│  MISSÃO (yv_status_som_test.py ou futura missão)            │
│  go("Envoyer") · pick_color_from_right(3) · expect/absent   │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  REMOTE (remote.py) — comando de TV                         │
│  refresh · go · move · swipe_until · dismiss_if_needed      │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  SOM (som.py) — percepção estruturada                       │
│  uiautomator dump → UiMark[] + campo aria + overlay         │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  PRECISION (precision.py) — localizar / hit / trajetória    │
│  canónico↔físico · hit inset · snap · swipe ease            │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  EXECUTOR (executor.py) — actuadores                        │
│  scrcpy control socket (preferido) · ADB shell persistente  │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  CAPTURE (capture.py + normalize + filter)                  │
│  frame BGR → 1080×1920 · filtro de mudança                  │
└─────────────────────────────────────────────────────────────┘
```

### Metáfora operacional

| Carro autónomo | ADB vision_agent |
|----------------|------------------|
| Sensores (câmara / GPS) | scrcpy frame + uiautomator XML |
| Mapa / lanes | SoM + aria-labels |
| Controlo (volante) | hit-point + inject touch |
| Feedback (IMU) | change de labels / pixels + settle |
| Recuperação | dismiss diálogo · bias no bounds · re-dump |

---

## 3. Espaços de coordenadas (crítico)

Há **três** espaços. Misturá-los causa toques no sítio errado.

| Espaço | Origem | Uso |
|--------|--------|-----|
| **Canónico** | 1080×1920 | Decisões, SoM, missões |
| **Físico** | `wm size` (ex.: 720×1600) | Toques scrcpy/ADB, bounds do XML |
| **Vídeo** | frame scrcpy (`max_size`) | Só captura visual |

**Regra de ouro:** SoM e toques usam sempre o tamanho **físico**.  
O stream de vídeo pode ter outra resolução — **nunca** usar `_video_w/h` para mapear taps.

Funções:

- `precision.map_canonical_to_physical(x, y, phys_w, phys_h)`
- `ActionExecutor.device_w/h` = físico
- `ScrcpyController.physical_size` vs `video_size`

---

## 4. Set-of-Mark (`som.py`)

### Pipeline

1. `uiautomator dump` → XML
2. Filtrar nós interativos (`clickable`, botões, EditText, ações Copy/Send…)
3. Deduplicar por IoU
4. Mapear bounds → canónico
5. Overlay numerado + catálogo textual

### `UiMark`

| Campo | Significado |
|-------|-------------|
| `id` | Número no overlay |
| `x1,y1,x2,y2` | Bounds canónicos |
| `label` | Texto preferido (text → content-desc → rid → class) |
| `cls` | Classe curta (`Button`, `EditText`…) |
| `aria` | `text \| content-desc \| resource-id` (navegação TV) |
| `hit` | Hit-point com inset (via `precision`) |

### Boas práticas

- Preferir `go("Nouveau message de statut")` a coordenadas fixas
- Match de aria é **estrito** (evita `Message` ≈ `message de statut`)
- Acentos normalizados (`Émeraude` ≡ `emeraude`) em `remote._norm`

---

## 5. Precisão (`precision.py`)

| Técnica | Função |
|---------|--------|
| Hit-point inset | Evita bordas mortas do Android (`HIT_INSET_*`) |
| Bias por classe | EditText / texto largo → toque mais à esquerda |
| Snap | Coord livre → marca SoM mais próxima / contentora |
| Settle | Pausa pós-gesto (`settle_for_action`) |
| Verify | Mudança de frame **ou** de fingerprint de labels |
| Autocorrect | Bias **dentro** do bounds (não offsets soltos no ecrã) |
| Swipe ease | Trajetória smoothstep no scrcpy |

**Lições:**

- Verify só por pixels gera falsos negativos (cor/fonte mudam pouco o XML)
- Offsets livres fora do alvo (espiral grande) destroem o fluxo (abrir Calls, Meta AI…)
- Swatches na **margem** (ex. x=18) devem ser **centrados** antes do tap

---

## 6. Controlo remoto (`remote.py`)

API principal: `RemoteNavigator`.

### Métodos

| Método | Comportamento |
|--------|----------------|
| `refresh(tag)` | Frame + SoM + `ex.set_marks` + screenshots |
| `go(*aria, expect=, absent=, max_tries=)` | Salta ao label; valida pós-condição; retenta |
| `move(dir, tap=False)` | Foco espacial up/down/left/right |
| `tap(mark, verify=, long=)` | Toque com hit-point; autocorrect se preciso |
| `dismiss_if_needed()` | Diálogo perigoso → Annuler/Cancel (nunca Abandonner) |
| `swipe_until(*aria, step_frac=, bidirectional=)` | Fila horizontal até ao alvo |
| `has` / `screen_looks_like` | Consultas ao fingerprint atual |

### Match aria (`find_by_aria` / `match_score`)

Prioridade típica:

1. Igualdade exacta (após normalizar acentos)
2. Needle contido no label/aria (frase ≥ 4 chars)
3. Todos os tokens ≥ 4 chars presentes
4. `min_score` default 350 — rejeita matches fracos

### Dismiss automático

Padrões de perigo: `abandonner`, `discard`, `supprimer`…  
Padrões de cancelar: `annuler`, `cancel`, `fechar`…  
Swipe agressivo na palette pode abrir “Abandonner le texte?” → dismiss e continuar com passo curto.

---

## 7. Executor (`executor.py`)

- Preferência: **scrcpy control** (baixa latência, Unicode clipboard)
- Fallback sticky: se scrcpy falhar a meio → ADB até ao fim da sessão
- `execute({acao, coordenadas, marca, verify, marks})`
- Acções: `click`, `long_click`, `swipe_up/down`, `write_text`, `aguardar`, `concluido`
- Shell ADB **persistente** (sem spawn por toque)

---

## 8. Missão de referência: `yv_status_som_test.py`

Fluxo validado:

| # | Passo | Técnica |
|---|-------|---------|
| 1 | Abrir Daniel 12:4 (S21) | deep link YouVersion |
| 2 | Long-press verso | SoM aria do texto |
| 3 | Copy | aria `copy` ou clipboard forçado (`VERSE_FALLBACK`) |
| 4 | WhatsApp Status | tab espacial / aria / `whatsapp://status` |
| 5 | Pencil texto | `Nouveau message de statut` |
| 6 | Colar verso | clipboard scrcpy paste |
| 7 | Cor | **`COLOR_FROM_RIGHT = 3`** (3ª do canto direito) |
| 8 | Fonte | `Sans Bold` via `swipe_until` |
| 9 | Envoyer | aria + expect ecrã Status |
| 10 | Validar | `FINAL_OK` / `status_published` |

### Cor (contrato actual)

```python
COLOR_FROM_RIGHT = 3  # 1=última (direita), 3=antepenúltima
pick_color_from_right(nav, index_from_right=COLOR_FROM_RIGHT)
```

Algoritmo:

1. Abrir palette (`Couleur de fond`)
2. Scroll para a **esquerda** até a fila estabilizar (fim direito)
3. Ordenar swatches por `cx`
4. Tocar `row[-N]`
5. Se colado à margem direita, inset e re-resolver o índice

Exemplo observado no fim da fila FR:

`Glycine | Bleu pluie | Gris clair | Étoile de mer | Éléphant | Rose brûlé | Scorpion`  
→ N=3 ⇒ **Éléphant**.

### Sucesso do envio

Não usar `statut` sozinho (casa com `écrivez un statut`).  
Preferir: `mes mises à jour de statut`, `vu par`, `nouveau message de statut`, `discussions`.

---

## 9. Variáveis de ambiente

| Env | Default | Notas |
|-----|---------|-------|
| `VISION_ADB_SERIAL` | 1º device | USB ou Wi‑Fi |
| `VISION_PRECISION_VERIFY` | `1` | Verify no executor |
| `VISION_HIT_INSET_FRAC` | `0.12` | Inset relativo |
| `VISION_HIT_INSET_MIN/MAX` | `4` / `22` | px canónicos |
| `VISION_SNAP_MAX_DIST` | `80` | Snap SoM |
| `VISION_PRECISION_MAX_RETRIES` | `4` | Retries |
| `VISION_AGENT_STEP_DELAY` | `1.0` | Escala settle |
| `VISION_SCRCPY_*` | bitrate/fps/max_size | Stream |
| `PYTHONIOENCODING=utf-8` | — | Consola Windows + acentos |

Settle típicos (`config.settle_for_action`):

- click ≈ 0.22 s  
- swipe ≈ 0.40 s  
- text ≈ 0.35 s  

---

## 10. Como escrever uma nova missão (checklist)

1. **Abrir app** — `am start` / deep link (mais estável que só taps)
2. `RemoteNavigator.refresh("01-...")` — guardar SoM + JPEG
3. Localizar alvo — `go("aria exacta")` ou `find_by_aria`
4. Definir **expect** / **absent** (pós-condições de ecrã)
5. Gestos de fila — `swipe_until` ou índice (`pick_color_from_right`)
6. Sempre `dismiss_if_needed` após swipes longos
7. Clipboard/Unicode — scrcpy `set_clipboard` (não `adb input text` com acentos)
8. Validar sucesso com fingerprint **específico** do ecrã final
9. Logs `print(..., flush=True)` + frames em `frames/<missão>/`

Modelo mínimo:

```python
nav = RemoteNavigator(ex, serial=SERIAL, adb_path=ADB, grab_fn=..., save_fn=...)
nav.refresh("01")
nav.go("Botão alvo", expect=("ecrã seguinte",), max_tries=3)
nav.dismiss_if_needed()
```

---

## 11. Anti-padrões (não repetir)

| Evitar | Porquê | Fazer em vez disso |
|--------|--------|--------------------|
| Mapear tap com resolução do vídeo | Desalinha SoM | Sempre `wm size` físico |
| Match aria frouxo (`Message` ⊂ needle) | Toques errados (Meta AI) | `match_score` estrito + `min_score` |
| Offset spiral fora do bounds | Sai do fluxo | Bias só dentro do mark |
| Verify pixel em swatch de cor | Falso negativo + retries | `verify=False` em cores |
| Expect `"statut"` genérico | Casa com composer | Frases completas de ecrã |
| Swipe full-width na palette | Abre Abandonner | Passo ~1–2 chips + dismiss |
| Coordenadas mágicas como 1ª opção | Fragilizam por resolução | Aria → espacial → coord |

---

## 12. Roadmap — próximos níveis

Sugestão de evolução **em cima** desta base (sem partir o contrato):

| Nível | Entrega | Depende de |
|-------|---------|------------|
| **L0** (actual) | Missões determinísticas + remote + precisão | — |
| **L1** | Biblioteca de ecrãs + predicção de atalho (`env_cache.py`) | `labels()` / SoM / `step_cache` |
| **L2** | Planeador simples (grafo via `transitions` + history) | L1 + `go`/`move` |
| **L3** | LLM escolhe `marca` / aria a partir do SoM | L0 + prompt |
| **L4** | Memória / skills por app (WhatsApp, Bible…) | L2/L3 |
| **L5** | Servidor / UI de missão (ex. antigo `server.py`) | L3+ |

Contrato estável para L3+:

```json
{
  "acao": "click",
  "marca": 8,
  "coordenadas": {"x": 540, "y": 960},
  "verify": false
}
```

O executor + remote já resolvem hit-point e físico.

---

## 13. Ficheiros-chave (índice)

| Ficheiro | Responsabilidade |
|----------|------------------|
| `config.py` | Constantes, settle, env |
| `capture.py` | Fontes de frame |
| `normalize.py` | 1080×1920 |
| `filter.py` | Ratio de mudança |
| `som.py` | Dump → marcas |
| `a11y.py` | A11y-first: árvore completa + click/scroll/wait (sem frame) |
| `precision.py` | Geometria de toque |
| `remote.py` | Navegação por aria (+ SoM/frame) |
| `env_cache.py` | L1: fingerprint + `predict_next` + PosCache |
| `executor.py` | Actuadores scrcpy/ADB |
| `yv_status_som_test.py` | Missão canónica / regressão |
| `loop.py` | Captura contínua Bloco 1 |

---

## 13b. A11y-first (sem frame)

Camada paralela a SoM/Remote: usa só a árvore de acessibilidade (`uiautomator dump`).

```bash
python -u -m vision_agent.a11y --dump
python -u -m vision_agent.a11y --click Books
python -u -m vision_agent.a11y --wait Envoyer --timeout 8
python -u -m vision_agent.a11y --scroll down
```

API:

```python
from vision_agent.a11y import A11yNavigator

nav = A11yNavigator()
nav.refresh()
nav.click("Books", "Livres")
nav.wait_for("EditText")
nav.scroll("down")
```

Quando usar: scripts sem visão, smoke de UI, missões novas.  
Missão Status estável continua em `RemoteNavigator` + SoM.

---

## 14. Critérios de regressão

Antes de mergear mudanças na base, correr:

```bash
set PYTHONIOENCODING=utf-8
set VISION_ADB_SERIAL=<device>
python -u -m vision_agent.yv_status_som_test
```

Esperado:

- Log `COR OK '…'` com a N-ésima do direito (`COLOR_FROM_RIGHT`)
- Fonte `Sans Bold` (ou política actual de tipografia)
- `FINAL_OK` e `sent= True`
- Sem diálogo Abandonner por resolver no fim

---

*Última base estável: fluxo YV→Status com cor = 3ª do canto direito, tipografia Sans Bold, envio confirmado.*
