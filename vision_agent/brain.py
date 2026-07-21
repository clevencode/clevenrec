"""Cérebro multimodal — OpenAI-compatible vision → JSON de ação."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any, Optional

import httpx

from .config import (
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_VISION_MODEL,
    PACKAGE_DIR,
)

PROMPT_PATH = PACKAGE_DIR / "prompts" / "system.md"


def load_system_prompt(objetivo: str) -> str:
    raw = PROMPT_PATH.read_text(encoding="utf-8")
    # Remove cercas markdown de exemplo para não confundir o modelo
    text = re.sub(r"```json.*?```", "", raw, flags=re.DOTALL)
    text = text.replace("**[OBJETIVO]**", objetivo).replace("[OBJETIVO]", objetivo)
    text += (
        "\n\nOBJETIVO ATUAL DO USUÁRIO:\n"
        f"{objetivo}\n\n"
        "Responda APENAS com um objeto JSON válido, sem markdown."
    )
    return text


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise ValueError("Resposta da IA sem JSON.")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("JSON da IA não é um objeto.")
    return data


def _normalize_decision(data: dict[str, Any]) -> dict[str, Any]:
    acao = str(data.get("acao") or "aguardar").strip().lower()
    status = str(data.get("status") or "em_andamento").strip().lower()
    coords = data.get("coordenadas")
    if isinstance(coords, dict):
        try:
            coords = {"x": int(coords.get("x")), "y": int(coords.get("y"))}
        except (TypeError, ValueError):
            coords = None
    else:
        coords = None
    return {
        "pensamento": str(data.get("pensamento") or ""),
        "status": status,
        "acao": acao,
        "coordenadas": coords,
        "texto_input": data.get("texto_input"),
    }


class VisionBrain:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else OPENAI_API_KEY
        self.base_url = (base_url or OPENAI_BASE_URL).rstrip("/")
        self.model = model or OPENAI_VISION_MODEL

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def decide(self, objetivo: str, image_path: Path) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY não definida. Configure a chave para o agente de visão."
            )
        jpeg = Path(image_path).read_bytes()
        b64 = base64.b64encode(jpeg).decode("ascii")
        system = load_system_prompt(objetivo)
        payload = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Analise este frame da tela Android (1080x1920) "
                                "e devolva a próxima ação JSON."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64}",
                                "detail": "low",
                            },
                        },
                    ],
                },
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"
        last_err: Optional[Exception] = None
        for _ in range(2):
            try:
                with httpx.Client(timeout=60.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    resp.raise_for_status()
                    body = resp.json()
                content = (
                    body.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                return _normalize_decision(_extract_json(content or ""))
            except Exception as exc:
                last_err = exc
        raise RuntimeError(f"Falha na decisão da IA: {last_err}")
