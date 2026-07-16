# AI Laboratory — Backend

FastAPI engine that turns a natural-language experiment brief into a sandboxed
Docker run, streaming live logs, artifacts, and metrics to the UI over a
WebSocket.

## Run

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then edit as needed
python main.py                # serves on http://localhost:8000
```

You need a running [Ollama](https://ollama.com) instance for the planning step
(`ollama pull qwen2.5-coder:7b`) and a Docker daemon for execution. The API
still boots without them — those failures surface only when you call
`/analyze` or `/approve`.

## Flow

1. `POST /api/experiments/analyze` — the Architect model drafts a `Plan`
   (returned; status `awaiting_approval`). Nothing runs yet.
2. `POST /api/experiments/{id}/approve` — codegen writes files into
   `experiments/{id}/`, then a worker builds the image and runs the container.
3. `WS /api/experiments/{id}/logs` — subscribe for the live typed stream.
   Reconnecting mid-run replays the recent buffer first.

## Sandbox

Experiment containers run with `--network none`, memory/CPU/PID caps, all
capabilities dropped, `no-new-privileges`, a read-only rootfs + writable tmpfs,
and a hard timeout. The host Docker socket is never mounted into an experiment.
Nested-container experiments use a rootless DinD sidecar / sysbox runtime
(`ALLOW_NESTED_DOCKER=true`), never the host socket.

## Log marker protocol

Experiment code prints these; `artifact_detector.py` turns them into typed
console events:

```
::iteration:: <n>                 -> iteration divider
::artifact:: <relative/path>      -> highlighted artifact chip
::metric:: <name> <value> [unit]  -> live metric gauge
[docker] / [bench] / [hypothesis] -> colorized category tag on a log line
```

The demo orchestrator that codegen writes when a plan ships no runnable
entrypoint emits all of these, so you can validate the full pipeline before
wiring in LLM-generated experiment code.
