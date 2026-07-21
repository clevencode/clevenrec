"""HTTP API do agente — central de comando (porta 8790)."""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .agent_loop import mission
from .config import AGENT_HOST, AGENT_PORT
from .brain import VisionBrain

app = FastAPI(title="ClevenRec Vision Agent", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class StartBody(BaseModel):
    objetivo: str = Field(..., min_length=1)
    serial: str | None = None


@app.get("/health")
def health():
    brain = VisionBrain()
    return {
        "ok": True,
        "brain_configured": brain.configured,
        "model": brain.model if brain.configured else None,
    }


@app.get("/agent/status")
def agent_status():
    return mission.get_status()


@app.post("/agent/start")
def agent_start(body: StartBody):
    return mission.start(body.objetivo, serial=body.serial)


@app.post("/agent/stop")
def agent_stop():
    return mission.stop()


def main() -> None:
    uvicorn.run(
        "vision_agent.server:app",
        host=AGENT_HOST,
        port=AGENT_PORT,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
