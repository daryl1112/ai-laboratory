"""AI lab orchestrator — FastAPI app.

Run natively on the host:  python run.py   (or: uvicorn server.main:app)
"""
from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .config import config, ensure_dirs
from .docker_manager import get_docker
from .models import ChatBody, ConfigPatch, NewExperiment, ToolTestBody
from .ollama_client import ollama
from .orchestrator import orchestrator
from .sysstats import system_stats
from .tool_loader import registry
from .ws import hub

UI_DIR = Path(__file__).resolve().parent.parent / "ui"

app = FastAPI(title="ailab", docs_url="/api/docs", openapi_url="/api/openapi.json")

_background: list[asyncio.Task] = []


@app.on_event("startup")
async def startup() -> None:
    ensure_dirs()
    await db.init()
    await _sync_tools()
    await orchestrator.start()
    _background.append(asyncio.create_task(_stats_loop()))
    _background.append(asyncio.create_task(_tools_watch_loop()))


@app.on_event("shutdown")
async def shutdown() -> None:
    for t in _background:
        t.cancel()
    await ollama.close()


async def _sync_tools() -> None:
    tools = registry.reload()
    await db.clear_tools()
    for t in tools:
        await db.upsert_tool(t.name, t.path, t.schema, t.status, t.error)
    await hub.broadcast("tools", {"tools": registry.summary()})


async def _tools_watch_loop() -> None:
    while True:
        await asyncio.sleep(3)
        try:
            if registry.maybe_reload():
                await _sync_tools()
        except Exception:
            pass


async def _stats_loop() -> None:
    loop = asyncio.get_running_loop()
    while True:
        try:
            stats = await system_stats()
            await hub.broadcast("stats_system", stats)
            for exp_id, run in list(orchestrator.active.items()):
                if run.container_id:
                    cstats = await loop.run_in_executor(
                        None, get_docker().container_stats, run.container_id)
                    if cstats:
                        await hub.broadcast("stats_container", {"exp": exp_id, **cstats})
        except Exception:
            pass
        await asyncio.sleep(3)


# ---- experiments -----------------------------------------------------------

@app.post("/api/experiments")
async def create_experiment(body: NewExperiment):
    options = {k: v for k, v in body.model_dump().items()
               if k != "prompt" and v is not None}
    return await orchestrator.create(body.prompt, options)


@app.get("/api/experiments")
async def list_experiments():
    return await db.list_experiments()


@app.get("/api/experiments/{exp_id}")
async def get_experiment(exp_id: str):
    exp = await db.get_experiment(exp_id)
    if not exp:
        raise HTTPException(404)
    exp.pop("conclusion_embedding", None)
    exp["events"] = await db.list_events(exp_id)
    exp["artifacts"] = await db.list_artifacts(exp_id)
    exp["services"] = await db.list_services(exp_id)
    return exp


@app.get("/api/experiments/{exp_id}/messages")
async def get_messages(exp_id: str):
    return await db.list_messages(exp_id)


@app.get("/api/experiments/{exp_id}/logs")
async def get_logs(exp_id: str, revision: int | None = None, tail: int = 500):
    exp = await db.get_experiment(exp_id)
    if not exp:
        raise HTTPException(404)
    rev = revision or exp.get("revision") or 1
    path = orchestrator.workspace(exp_id) / "logs" / f"run-r{rev}.log"
    if not path.exists():
        return {"revision": rev, "lines": []}
    lines = path.read_text(errors="replace").splitlines()[-tail:]
    return {"revision": rev, "lines": lines}


@app.get("/api/experiments/{exp_id}/similar")
async def get_similar(exp_id: str):
    return await db.similar_experiments(exp_id)


@app.post("/api/experiments/{exp_id}/approve")
async def approve(exp_id: str):
    await orchestrator.approve(exp_id)
    return {"ok": True}


@app.post("/api/experiments/{exp_id}/reject")
async def reject(exp_id: str):
    await orchestrator.reject(exp_id)
    return {"ok": True}


@app.post("/api/experiments/{exp_id}/stop")
async def stop(exp_id: str):
    await orchestrator.stop(exp_id)
    return {"ok": True}


@app.post("/api/experiments/{exp_id}/pause")
async def pause(exp_id: str):
    await orchestrator.pause(exp_id)
    return {"ok": True}


@app.post("/api/experiments/{exp_id}/resume")
async def resume(exp_id: str):
    await orchestrator.resume(exp_id)
    return {"ok": True}


@app.post("/api/experiments/{exp_id}/rerun")
async def rerun(exp_id: str):
    exp = await orchestrator.rerun(exp_id)
    if not exp:
        raise HTTPException(404)
    return exp


@app.delete("/api/experiments/{exp_id}")
async def delete(exp_id: str):
    await orchestrator.delete(exp_id)
    return {"ok": True}


@app.post("/api/experiments/{exp_id}/chat")
async def chat(exp_id: str, body: ChatBody):
    exp = await db.get_experiment(exp_id)
    if not exp:
        raise HTTPException(404)
    from .agent import run_chat
    tail = orchestrator._log_tail(exp_id, exp.get("revision") or 1)
    answer = await run_chat(exp_id, body.message, tail)
    return {"answer": answer}


@app.get("/api/experiments/{exp_id}/artifacts/{path:path}")
async def download_artifact(exp_id: str, path: str):
    base = (orchestrator.workspace(exp_id) / "output").resolve()
    target = (base / path).resolve()
    if not str(target).startswith(str(base)) or not target.is_file():
        raise HTTPException(404)
    return FileResponse(target)


# ---- tools ----------------------------------------------------------------

@app.get("/api/tools")
async def list_tools():
    return registry.summary()


@app.post("/api/tools/reload")
async def reload_tools():
    await _sync_tools()
    return registry.summary()


@app.post("/api/tools/{name}/test")
async def test_tool(name: str, body: ToolTestBody):
    result = await registry.run(name, body.args)
    return {"result": result}


# ---- services ----------------------------------------------------------------

@app.get("/api/services")
async def all_services():
    return await db.list_services()


@app.post("/api/services/{service_id}/{op}")
async def service_op(service_id: int, op: str):
    if op not in ("start", "stop", "delete"):
        raise HTTPException(400)
    rows = await db.list_services()
    svc = next((s for s in rows if s["id"] == service_id), None)
    if not svc:
        raise HTTPException(404)
    loop = asyncio.get_running_loop()
    with contextlib.suppress(Exception):
        await loop.run_in_executor(
            None, get_docker().service_container_op, svc["container_name"], op)
    if op == "delete":
        await db.delete_service(service_id)
    else:
        await db.set_service_state(service_id, "running" if op == "start" else "stopped")
    return {"ok": True}


# ---- system / config ---------------------------------------------------------

@app.get("/api/system/stats")
async def stats():
    return await system_stats()


@app.get("/api/system/models")
async def models():
    return {"models": await ollama.list_models()}


@app.post("/api/system/prune-images")
async def prune_images():
    keep = {e["id"] for e in await db.list_experiments(limit=10000)}
    loop = asyncio.get_running_loop()
    removed = await loop.run_in_executor(
        None, get_docker().prune_experiment_images, keep)
    return {"removed": removed}


@app.get("/api/config")
async def get_config():
    return config.data


@app.patch("/api/config")
async def patch_config(body: ConfigPatch):
    return config.patch(body.patch)


# ---- websocket -----------------------------------------------------------------

@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()   # client pings; content ignored
    except WebSocketDisconnect:
        await hub.disconnect(ws)


# ---- static UI (mounted last so /api and /ws win) --------------------------------

if UI_DIR.exists():
    app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")
