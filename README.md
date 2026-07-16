# ailab — a personal AI experiment laboratory

A local model (via Ollama) acts as the resident scientist: you describe an
experiment in the UI, the model researches it with your tools, writes the code,
launches it in a Docker container, checks in on it while it runs, revises the
code when it fails, and writes a conclusion when it's done.

The orchestrator runs **natively on the host** and talks to the local Docker
daemon. Only the supporting services (Ollama, Postgres, pgAdmin) are containers.

## Layout

```
ailab/
├─ docker-compose.yml     Ollama (ROCm) + Postgres/pgvector + pgAdmin
├─ base-image/            fat CPU-only ML image experiments run on
├─ server/                FastAPI orchestrator (runs on the host)
├─ ui/                    single-page UI served by the orchestrator
├─ tools/                 example tool files (copy into $AILAB_HOME/tools)
└─ run.py                 entry point
```

Runtime data lives in `$AILAB_HOME` (default `~/ailab-data`):
`experiments/<id>/` (plan.json, code, `revisions/rN/`, `logs/run-rN.log`,
`output/`), `tools/` (hot-loaded tool files), `config.json` (your overrides).

## Setup

1. **Services**

   ```bash
   cp .env.example .env        # adjust passwords if you like
   docker compose up -d
   ```

2. **Models** (both lab models + the embedding model)

   ```bash
   docker exec ollama ollama pull qwen3:30b-a3b-instruct-2507
   docker exec ollama ollama pull qwen3-coder:30b
   docker exec ollama ollama pull nomic-embed-text
   ```

3. **Orchestrator** (host, Python 3.12+)

   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python run.py               # UI at http://127.0.0.1:8080
   ```

4. **Tools** — copy the examples and add your own:

   ```bash
   mkdir -p ~/ailab-data/tools && cp tools/*.py ~/ailab-data/tools/
   ```

The base experiment image (`ailab-base:py312`) builds automatically in the
background on first start; the first experiment may wait a few minutes for it.
Build it ahead of time with `docker build -t ailab-base:py312 base-image/`.

## How a run works

```
queued → planning → awaiting_approval → provisioning → running ⇄ paused
                                                     ↘ completed | failed | stopped
```

- **Planning.** The model gets your prompt plus every loaded tool, researches as
  needed, and returns a plan: title, objective, success criteria, environment
  (base image + pip requirements, or a custom Dockerfile), files (must include
  `main.py`), optional services (postgres/redis), and resource limits.
- **Approval.** By default you review the plan (including all code) before
  anything launches. Toggle per experiment.
- **Run.** The container runs CPU-only with your CPU/RAM caps, on a private
  network with its services. Logs stream live to the UI and to
  `logs/run-rN.log`. Lines matching `##PROGRESS <pct> <message>##` drive the
  progress bar.
- **Check-ins.** Every N minutes (and on stalls or exit) the model sees the log
  tail and decides: continue, abort, revise, or conclude. A revision rewrites
  the files (snapshotted under `revisions/`), relaunches, and increments the
  revision counter — up to `limits.max_revisions`.
- **Conclusion.** The model writes a lab-notebook conclusion, which is embedded
  (pgvector) so related past experiments surface via
  `GET /api/experiments/<id>/similar`.

Everything Docker-side carries the labels `ailab.exp=<id>` and
`ailab.managed=1`, so cleanup is label-based: deleting an experiment removes
its containers, network, volumes, images, and workspace. Services declared with
`persist: true` are stopped but kept, and managed from the Services page.

## Tool files

A tool is one `.py` file in `$AILAB_HOME/tools`:

```python
SCHEMA = {
    "type": "function",
    "function": {
        "name": "extract_links",
        "description": "Extract all hyperlinks from a webpage",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Page URL"}},
            "required": ["url"],
        },
    },
}

def execute(url):
    ...
    return {"success": True, "links": [...]}
```

Files hot-reload on change. Broken files show as error cards in the Tools page
instead of crashing the loader. Execution is sandboxed to a worker thread with
a timeout, and results are truncated before entering model context.

Because Ollama's native tool-calling is unreliable for some models, the default
transport is `json` (schemas in the prompt, parsed JSON replies). Switch to the
native API in Config → `model.tool_transport: "native"`.

## Models

`model.per_phase` routes phases to models — planning/conclusions/chat to the
instruct model with thinking on, code and check-ins to the coder with thinking
off. Override with one model per experiment from the New Experiment form, or
change defaults in Config. Every stored message records which model (and
thinking mode) produced it, so you can compare.

## API

`/api/docs` has the interactive reference. Highlights:

```
POST   /api/experiments               create (prompt + options)
GET    /api/experiments[/<id>]        list / detail (events, artifacts, services)
POST   /api/experiments/<id>/approve|reject|stop|pause|resume|rerun
DELETE /api/experiments/<id>          full teardown
GET    /api/experiments/<id>/logs|messages|similar
POST   /api/experiments/<id>/chat     ask the scientist about a run
GET    /api/tools                     POST /api/tools/reload, /api/tools/<name>/test
GET    /api/services                  POST /api/services/<id>/start|stop|delete
GET/PATCH /api/config                 live-editable settings
POST   /api/system/prune-images       remove images of deleted experiments
WS     /ws                            logs, status, progress, traces, stats
```
