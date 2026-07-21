# vision_agent — Bloco 1 + Central de Agente

Captura ADB + filtro de estabilidade + **agente IA** (visão → JSON → toque scrcpy/ADB).

## Setup

```bash
cd C:\Users\Clevy\Projects\clevenrec
python -m venv .venv
.venv\Scripts\activate
pip install -r vision_agent\requirements.txt
```

Defina a chave da IA (OpenAI-compatible):

```bash
set OPENAI_API_KEY=sk-...
rem opcional:
set OPENAI_BASE_URL=https://api.openai.com/v1
set OPENAI_VISION_MODEL=gpt-4o-mini
```

## Captura (Bloco 1)

```bash
python -m vision_agent.loop
python -m vision_agent.loop --show --max-frames 5
```

## Servidor do agente (central)

```bash
python -m vision_agent.server
```

API em `http://127.0.0.1:8790`:

- `GET /health`
- `POST /agent/start` `{"objetivo":"..."}`
- `POST /agent/stop`
- `GET /agent/status`

No ClevenRec, o card **Agente** sobe esse servidor automaticamente (via `.venv`).

## Arquitetura

1. Captura frame (ADB screencap) → 1080×1920  
2. Filtro de mudança  
3. Visão multimodal → JSON (`prompts/system.md`)  
4. Execução: scrcpy control socket (rápido) com fallback ADB  

## Prompt

Ver [prompts/system.md](prompts/system.md).
