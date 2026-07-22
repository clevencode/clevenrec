# vision_agent — base de navegação ADB

Automação de telemóvel Android por **ADB + scrcpy + Set-of-Mark + comando remoto (aria-label)**.  
Validado ponta-a-ponta: YouVersion → WhatsApp Status (texto + cor + fonte + enviar).

**Sem OpenAI / sem servidor de agente** nesta base. A IA pode ser um nível seguinte em cima desta pilha.

---

## Setup rápido

```bash
cd C:\Users\Clevy\Projects\clevenrec
python -m venv .venv
.venv\Scripts\activate
pip install -r vision_agent\requirements.txt
```

Variáveis úteis:

| Env | Função |
|-----|--------|
| `VISION_ADB_SERIAL` | Serial USB ou `IP:5555` (Wi‑Fi). Se vazio, usa o 1º device online |
| `ADB_PATH` | Caminho do `adb.exe` |
| `SCRCPY_DIR` | Pasta com `scrcpy.exe` / `scrcpy-server` |

```bash
# Captura contínua (filtro de mudança)
python -m vision_agent.loop --show --max-frames 5

# Missão de referência (YouVersion → Status)
set VISION_ADB_SERIAL=192.168.1.161:5555
set PYTHONIOENCODING=utf-8
python -u -m vision_agent.yv_status_som_test
```

Frames do teste: `vision_agent/frames/yv_som_test/`.

---

## Documentação

| Doc | Conteúdo |
|-----|----------|
| **[docs/BASE_NAVEGACAO.md](docs/BASE_NAVEGACAO.md)** | Arquitetura, camadas, APIs, lições aprendidas, roadmap de níveis |
| Este README | Setup + mapa do pacote + comandos |

---

## Mapa do pacote

```
vision_agent/
├── capture.py      # Frame: scrcpy H.264 ou ADB screencap
├── normalize.py    # → 1080×1920 canónico
├── filter.py       # Mudança de pixels (estabilidade)
├── som.py          # Set-of-Mark (uiautomator → marcas + aria)
├── precision.py    # Hit-point, snap, mapa físico, swipe suave
├── remote.py       # Controlo remoto TV (go / move / swipe / dismiss)
├── executor.py     # Toques scrcpy control + fallback ADB
├── config.py       # Constantes + env
├── loop.py         # Loop de captura (Bloco 1)
├── yv_status_som_test.py   # Missão de referência (10/10)
└── docs/BASE_NAVEGACAO.md  # Base para próximos níveis
```

---

## Ideia em uma frase

Tratar o ecrã como um **carro autónomo controlado por comando de TV**:  
perceber (SoM/aria) → localizar (coords físicas) → actuar (hit-point) → verificar → autocorrigir.
