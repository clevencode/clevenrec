# vision_agent — Bloco 1

Captura de tela via ADB + filtro de estabilidade (OpenCV).  
Ainda **não** chama API de IA (Bloco 3) nem injeta toques via scrcpy TCP (Bloco 5).

## Fluxo

1. `adb exec-out screencap -p` → frame BGR  
2. Resize para **1080×1920**  
3. Compara com o último frame aprovado (absdiff)  
4. Se mudança ≥ 2%, grava JPEG em `frames/` e loga JSON  

## Setup

```bash
cd C:\Users\Clevy\Projects\clevenrec
python -m venv .venv
.venv\Scripts\activate
pip install -r vision_agent\requirements.txt
```

Requer `adb` (vem com scrcpy) e celular com depuração USB (ou `adb connect IP:5555`).

## Uso

Na raiz do repo:

```bash
python -m vision_agent.loop
python -m vision_agent.loop --show
python -m vision_agent.loop --serial LMK410HMYP8HSWCIUO --max-frames 5
```

Variáveis opcionais:

- `VISION_ADB_SERIAL` — serial padrão  
- `ADB_PATH` — caminho do `adb.exe`  

## Prompt (stub)

Ver [prompts/system.md](prompts/system.md) — schema JSON das ações para o Bloco 3.
