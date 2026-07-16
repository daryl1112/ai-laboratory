# AI Laboratory

An autonomous AI experiment lab. Describe an experiment in natural language; a
local Ollama model drafts a concrete build plan; you approve it; the experiment
is generated into `experiments/<id>/`, built, and run inside an isolated Docker
container while logs, artifacts, and metrics stream live to a command-center UI.

```
ai-laboratory/
├── backend/          FastAPI engine (planner, codegen, docker manager, log bus)
├── ui/               Next.js command-center interface
├── experiments/      generated experiment code + results (one folder per run)
└── docker-compose.yml
```

## Quickstart (Docker Compose)

```bash
cp .env.example .env
# set LAB_ROOT to the ABSOLUTE path of this folder, e.g.
#   LAB_ROOT=/Users/you/ai-laboratory
docker compose up --build
# pull the model once (first run only):
docker compose exec ollama ollama pull qwen2.5-coder:7b
```

Then open the UI at http://localhost:3000 (backend API on :8000, Ollama on :11434).

## Flow

1. New experiment → paste a brief → Analyze. The architect model returns a
   structured plan (files, libraries, tools, Docker spec, success criteria, risks).
2. Review the plan → Approve & launch. Code is generated into `experiments/<id>/`,
   the image is built, and the sandboxed container starts.
3. Live console streams tagged logs, highlights created artifacts, and updates
   metric gauges in real time over a WebSocket.
4. Every run appears on the dashboard with its status and latest metrics.

## Local dev (without Compose)

Backend:
```bash
cd backend && pip install -r requirements.txt && python main.py
```
UI:
```bash
cd ui && npm install && npm run dev
```
Requires a Docker daemon and a running Ollama (`ollama serve`).

## Safety model

Experiment containers run with `--network none`, memory/CPU/PID caps, all Linux
capabilities dropped, `no-new-privileges`, a read-only root filesystem plus a
writable tmpfs, and a hard timeout. The host Docker socket is mounted only into
the trusted backend, never into experiment containers. Experiments that need to
spawn their own containers use a rootless DinD / sysbox path
(`ALLOW_NESTED_DOCKER=true`) rather than the host socket.

See `backend/README.md` for the API and the log marker protocol.
