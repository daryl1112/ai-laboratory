"""Turns an approved Plan into files inside experiments/<id>/.

Writes plan.json, a Dockerfile derived from the plan, and every file the plan
declared. It guarantees a runnable entrypoint: if the plan didn't ship an
orchestrator with real content, a demo loop is written so the full
build -> run -> stream -> artifact -> metrics pipeline is testable end to end
without depending on an LLM to produce correct code.

NOTE: LLM-driven file generation (filling PlanFile.content via the coder model)
is the intended extension point here — wire it in ollama_client + this module.
"""
from __future__ import annotations

import json
from pathlib import Path

from config import settings
from schemas import Plan, PlanFile

_CODEGEN_SYSTEM = """You are a senior engineer implementing one file of an
experiment that runs headless inside a Docker container. Output ONLY the raw
file contents for the requested path — no markdown fences, no commentary.

The orchestrator/entrypoint MUST drive the experiment loop and print progress
using these exact markers so the lab console can parse them:
  ::iteration:: <n>
  ::artifact:: <relative/path>
  ::metric:: <name> <value> <unit>
Use print(..., flush=True). Write results to benchmark_history.json.
Keep imports limited to the plan's declared libraries plus the stdlib."""


def _llm_fill(plan: Plan, f: PlanFile) -> str:
    from services.ollama_client import chat_text  # lazy: avoids import if unused

    ctx = (
        f"Experiment: {plan.title}\nObjective: {plan.objective}\n"
        f"Summary: {plan.summary}\nLibraries: {', '.join(plan.libraries)}\n"
        f"All files: {', '.join(x.path for x in plan.files)}\n\n"
        f"Now write the file `{f.path}` (purpose: {f.purpose})."
    )
    code = chat_text(_CODEGEN_SYSTEM, ctx, model=plan.language and None)
    # strip accidental markdown fences
    code = code.strip()
    if code.startswith("```"):
        code = code.split("\n", 1)[-1]
        if code.rstrip().endswith("```"):
            code = code.rstrip()[:-3]
    return code.strip() + "\n"

_DEMO_ORCHESTRATOR = '''\
"""Auto-generated demo orchestrator.

Emits the tagged log lines, artifacts, and metrics the lab console understands,
so the execution + streaming pipeline can be validated immediately.
Replace with real experiment code (or LLM-generated code) in production.
"""
import json, random, time, pathlib

TARGET_ITERS = 5
history = []

for i in range(1, TARGET_ITERS + 1):
    print(f"::iteration:: {i}", flush=True)
    print(f"[hypothesis] iteration {i}: tuning parameters toward targets", flush=True)
    time.sleep(1)

    acc = round(min(0.99, 0.80 + i * 0.03 + random.uniform(-0.01, 0.02)), 3)
    comp = round(min(0.7, 0.40 + i * 0.04), 3)
    latency = round(max(40, 120 - i * 10 + random.uniform(-5, 5)), 1)

    # Emit a machine-readable artifact marker the detector picks up.
    art = pathlib.Path(f"artifacts/step_{i}.json")
    art.parent.mkdir(exist_ok=True)
    art.write_text(json.dumps({"iteration": i, "accuracy": acc}))
    print(f"::artifact:: {art}", flush=True)

    # Emit metric markers: ::metric:: name value unit
    print(f"::metric:: accuracy {acc} percent", flush=True)
    print(f"::metric:: compression {comp} percent", flush=True)
    print(f"::metric:: latency {latency} ms", flush=True)

    history.append({"iteration": i, "accuracy": acc, "compression": comp, "latency_ms": latency})
    pathlib.Path("benchmark_history.json").write_text(json.dumps(history, indent=2))
    print(f"::artifact:: benchmark_history.json", flush=True)
    print(f"[bench] iteration {i} complete  acc={acc}  comp={comp}  lat={latency}ms", flush=True)

print("[done] target reached, exiting loop", flush=True)
'''


def _dockerfile(plan: Plan) -> str:
    pkgs = plan.docker.system_packages
    apt = ""
    if pkgs:
        apt = (
            "RUN apt-get update && apt-get install -y --no-install-recommends "
            + " ".join(pkgs)
            + " && rm -rf /var/lib/apt/lists/*\n"
        )
    pip = ""
    if plan.libraries:
        pip = "RUN pip install --no-cache-dir " + " ".join(plan.libraries) + "\n"
    entry = json.dumps(plan.docker.entrypoint)
    return (
        f"FROM {plan.docker.base_image}\n"
        "WORKDIR /experiment\n"
        f"{apt}"
        f"{pip}"
        "COPY . /experiment\n"
        f"ENTRYPOINT {entry}\n"
    )


def materialize(exp_id: str, plan: Plan) -> Path:
    """Write all files for an experiment and return its directory."""
    root = settings.experiments_dir / exp_id
    root.mkdir(parents=True, exist_ok=True)

    (root / "plan.json").write_text(plan.model_dump_json(indent=2))
    (root / "Dockerfile").write_text(_dockerfile(plan))
    (root / "logs").mkdir(exist_ok=True)

    entry_file = plan.docker.entrypoint[-1] if plan.docker.entrypoint else "orchestrator.py"
    wrote_entry = False

    for f in plan.files:
        target = (root / f.path).resolve()
        # Contain writes strictly inside the experiment folder.
        if not str(target).startswith(str(root.resolve())):
            raise ValueError(f"Refusing to write outside experiment dir: {f.path}")
        target.parent.mkdir(parents=True, exist_ok=True)

        content = f.content
        if not content.strip() and settings.llm_codegen:
            try:
                content = _llm_fill(plan, f)
            except Exception:
                content = ""  # fall back to stub / demo below

        target.write_text(content or f"# TODO: implement {f.path}\n# {f.purpose}\n")
        if f.path == entry_file and content.strip():
            wrote_entry = True

    if not wrote_entry:
        (root / entry_file).write_text(_DEMO_ORCHESTRATOR)

    return root
