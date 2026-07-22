---
name: ambiente-atalho
description: >-
  Atalho alimentado por análise preditiva de cache de visão do ambiente, com
  intenção de estilo fixa e antecedência de movimentos (scroll directo ao alvo
  conhecido). Use em vision_agent, ADB, PosCache, step_cache, missões Status,
  Éléphant/Sans Bold, predicção, L1 fingerprints, ou quando o utilizador guia
  pelo comportamento de postar status sem analisar outras cores/tipografias.
---

# Ambiente → atalho preditivo + antecedência

**Princípio base:** analisar o ambiente, use atalho quando perceber que já viu este ambiente.

**Upgrade (verbatim):** atalho alimentado por analise preditiva de cache de visao do ambiente.

## Guia de comportamento (fonte de verdade do negócio)

Quando vou postar um status, eu priorizo sempre a cor Elephant e a tipografia sans bold, isto faz com que nao perco tempo analisando outras cores ou tipografia pois ja sei o que quero atingir, ai sem perder tempo scrollo direto até a tipografia desejado usando o que eu chamo de antecedencia de movimentos, ja sei onde ficam a posicao da cor e da tipografia entao perco menos tempo analisando cores e tipografia indesejadas.

Tradução operacional:

| Comportamento humano | Regra da skill / código |
|----------------------|-------------------------|
| Sempre Éléphant + Sans Bold | `intent.color` / `intent.font` no JSON — não negociar alternativas |
| Não analisar outras opções | Ignorar labels intermédios da fila; só procurar o alvo da intenção |
| Antecedência de movimentos | Burst de scrolls na direcção conhecida + hit/aria do cache |
| Já sei a posição | `steps.*.hit` + `last_label` alimentam o atalho preditivo |

## Implementação canónica

| Peça | Onde | Papel |
|------|------|--------|
| API L1 | `vision_agent/env_cache.py` | `predict_next`, `get_intent`, `anticipate_pick`, `PosCache` |
| Negócio | `step_cache_*.json` | `intent`, `checkpoints`, `transitions`, `steps.*.hit` |
| Missão | `yv_status_som_test.py` | orquestra; não hardcodar estilo |
| Contrato | `docs/BASE_NAVEGACAO.md` | L1 → L2 |

```python
from vision_agent.env_cache import predict_next, get_intent, anticipate_pick, PosCache

intent = get_intent(cache)          # Éléphant / Sans Bold
pred = predict_next(nav.labels(), cache)
if pred.should_shortcut():
    anticipate_pick(nav, cache, kind="color")  # burst → só o alvo
```

## Ciclo preditivo + antecedência

```
1. OBSERVAR     → refresh SoM
2. CLASSIFICAR  → score(env | labels)
3. INTENT       → ler intent do cache (não explorar alternativas de estilo)
4. PREVER       → transitions + history ok
5. ANTECIPAR    → se painel conhecido: burst scroll na direcção do hit
                  → tocar só se label ∈ intent (aria exacta) ou hit
6. NÃO ANALISAR → labels indesejados da fila = ruído; não decidir com eles
7. ACTUALIZAR   → record hit/last_label se acertou o intent
```

## Confiança → acção

| Confiança | Acção |
|-----------|--------|
| **alta** | antecedência: N bursts + aria/hit do intent |
| **media** | 1 verify curto + hit; se miss → 1 burst + aria |
| **baixa** | ainda preferir intent; só então fallback `from_right` / scroll curto |
| **miss** | `retry_from_last`; não resetar missão; não “provar” outras cores |

## Boas práticas

1. **Intenção no JSON** — `intent.color` / `intent.font`; missão lê, não inventa.
2. **Antecedência > exploração** — bursts fixos do cache; não enumerar swatches.
3. **Objectivo final** — Envoyer manda; polish de estilo cede se o estado apertar.
4. **History só `ok=true`** — prior de posição.
5. **Aria específica** — Éléphant / Sans Bold exactos; proibido escolher “qualquer cor perto”.
6. **Coords** — hit canónico → toque físico; nunca vídeo scrcpy.
7. **Dismiss** antes de atalhar.
8. **Alimentar cache** — só gravar hit se `last_label` casar com o intent.

## Anti-padrões

- Avaliar Monte Carlo / Pourpre / Calistoga como candidatos
- Scroll “até ver algo giro” em vez de burst até ao alvo conhecido
- Duplicar predicção na missão
- Usar trail `ok=false` como prior de posição
- Atalhar por cima de diálogo Abandonner
