---
name: falha-inesperada
description: >-
  Quando houver uma falha inesperada (tela/overlay a sobrepor-se sem querer,
  diálogo, app errada), usa um atalho para voltar onde estava (último checkpoint
  / last_good) em vez de reiniciar a missão. Use em vision_agent, ADB, PosCache,
  overlays (agenda, notificação, Abandonner), ensure_composer, retry_from_last,
  ou quando o ecrã actual não casa com o passo esperado.
---

# Falha inesperada → atalho de regresso

**Princípio (verbatim):** quando houver uma falha inesperada, tipo uma tela se sobrepondo sem querer etc: use um atalhe para voltar onde estava.

Não explorar a UI intrusa. Não recomeçar a missão do zero. **Fechar / sair** com o gesto mais barato e **saltar de volta** ao último ponto bom (`last_good` / checkpoint).

## O que conta como falha inesperada

| Sinal | Exemplo |
|-------|---------|
| Overlay / outra app | Agenda “Prière…”, notificação full-screen, `Fermer` |
| Diálogo perigoso | `Abandonner le texte?` |
| Painel errado | Esperava `color_panel` / `font_panel`, viu `yv_verse` ou `unknown` |
| Predição absurda | `predict_next` → step que não faz sentido no fluxo actual |
| Labels zero overlap | Checkpoint esperado score ≈ 0 |

## Escada de recuperação (obrigatória)

```
1. DETECTAR   → refresh SoM; fingerprint ≠ passo esperado
2. DESCARTAR  → atalho leve na intrusa (não navegar nela)
3. REGRESSAR  → atalho para last_good / checkpoint
4. RETOMAR    → reexecutar só o passo interrompido
5. SE FALHAR  → retry_from_last (máx 2–3); objectivo final ainda manda
```

### 2) Descartar a intrusa (barato → caro)

1. `dismiss_if_needed()` — Annuler / Cancel (nunca Abandonner)
2. Aria de fecho: `Fermer`, `Close`, `Retour` (só se for overlay)
3. `keyevent BACK` (4) uma vez
4. Se ainda intrusa → `ensure_composer` / deeplink do checkpoint (salta a UI estranha)

### 3) Voltar onde estava

| `last_good` / contexto | Atalho de regresso |
|------------------------|--------------------|
| composer / paste / color / font | `ensure_composer` → `whatsapp://status` + pencil |
| color_panel a meio | composer → retap palette |
| font_panel a meio | composer → retap AA (Sans Serif) |
| status_tab | deeplink Status |
| yv_verse | deeplink verso (só se ainda na fase YV) |
| desconhecido | `transitions.unknown` → `ensure_composer` |

Prioridade: **checkpoint em cache > deeplink > BACK spam**.

## Implementação canónica

| Peça | Onde |
|------|------|
| `PosCache.ensure_composer` / `retry_from_last` | `vision_agent/env_cache.py` |
| `dismiss_if_needed` | `vision_agent/remote.py` |
| Overlay pós-palette (Fermer + retentar) | `yv_status_som_test.py` `do_color` |
| Checkpoints / unknown→recover | `step_cache_*.json` |

```python
# padrão
nav.refresh("guard")
if not expected_panel(nav):
    nav.dismiss_if_needed()
    if intrusion(nav):           # Fermer / agenda / etc.
        dismiss_intrusion(nav)   # Fermer ou BACK
    pos.ensure_composer()        # atalho: voltar onde estava
    return retry_step()          # retomar o passo, não a missão
```

## Regras

1. **Atalho > exploração** da tela que se sobrepôs.
2. **Guardar `last_good`** antes de cada fase; recuperação aponta para ele.
3. **Não gravar hits** da UI intrusa no cache de visão.
4. **Abortar antecedência** se o painel não for o esperado (`ANTECIP … abort`).
5. **Objectivo final** — após regresso, continuar o fluxo; polish cede a Envoyer.
6. Ligação: skill `ambiente-atalho` classifica o ecrã; esta skill **recupera** quando a classificação é intrusa / miss.

## Anti-padrões

- Scroll/analisar a app que apareceu por cima
- `force-stop` de tudo e recomeçar YV do zero por um overlay
- Tocar coords de hit (Éléphant) em cima da agenda
- Aceitar `Abandonner` para “sair mais depressa”
- Apagar `last_good` / hits bons só porque houve uma falha
