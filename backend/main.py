"""AI Laboratory backend — FastAPI app.

REST:
  POST /api/experiments/analyze     brief -> Plan (awaiting approval)
  GET  /api/experiments             list all runs (dashboard)
  GET  /api/experiments/{id}        full detail incl plan, metrics, artifacts
  POST /api/experiments/{id}/approve  generate code, build, run
  POST /api/experiments/{id}/stop
WebSocket:
  WS   /api/experiments/{id}/logs   live typed stream (log|artifact|metric|iteration|status)
"""
from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from db import Store
from schemas import AnalyzeRequest
from services.experiment_manager import ExperimentManager
from services.log_bus import bus

settings.ensure_dirs()
store = Store(settings.db_path)
manager = ExperimentManager(store)

app = FastAPI(title="AI Laboratory", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup():
    bus.bind_loop(asyncio.get_running_loop())


@app.get("/api/health")
async def health():
    return {"ok": True, "model": settings.architect_model, "network": settings.container_network}


@app.post("/api/experiments/analyze")
async def analyze(req: AnalyzeRequest):
    # planner is blocking (Ollama call) -> run in a thread
    exp = await asyncio.to_thread(manager.analyze, req.prompt, req.model)
    return exp


@app.get("/api/experiments")
async def list_experiments():
    return store.list()


@app.get("/api/experiments/{exp_id}")
async def get_experiment(exp_id: str):
    exp = store.get(exp_id)
    if not exp:
        raise HTTPException(404, "experiment not found")
    return exp


@app.post("/api/experiments/{exp_id}/approve")
async def approve(exp_id: str):
    try:
        return manager.approve(exp_id)
    except KeyError:
        raise HTTPException(404, "experiment not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/experiments/{exp_id}/stop")
async def stop(exp_id: str):
    manager.stop(exp_id)
    return {"stopped": exp_id}


@app.websocket("/api/experiments/{exp_id}/logs")
async def logs_ws(ws: WebSocket, exp_id: str):
    await ws.accept()
    q = await bus.subscribe(exp_id)
    try:
        while True:
            msg = await q.get()
            await ws.send_json(msg.model_dump())
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(exp_id, q)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
