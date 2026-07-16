"""The 'Architect' pass: brief -> structured Plan.

Sends the user's experiment brief to Ollama wrapped in a system prompt that
pins the exact JSON shape (schemas.Plan), then validates the result.
"""
from __future__ import annotations

import json

from schemas import Plan
from services.ollama_client import chat_json

_SYSTEM = """You are the Architect for an autonomous AI research laboratory.
A user gives you an experiment brief. You do NOT run anything. You produce a
single JSON object describing how the experiment would be built and evaluated
inside an isolated Docker container.

Return ONLY valid JSON matching exactly this shape:
{
  "title": "short human title",
  "objective": "one sentence goal",
  "summary": "2-4 sentence plain-language description of what will be built and how it is evaluated",
  "language": "python",
  "libraries": ["pip package names the experiment needs"],
  "tools": ["named helper tools/modules the harness provides, e.g. benchmark_logger"],
  "files": [
    {"path": "relative/path.py", "purpose": "what this file does", "content": ""}
  ],
  "docker": {
    "base_image": "python:3.12-slim",
    "system_packages": ["apt packages if any"],
    "entrypoint": ["python", "orchestrator.py"]
  },
  "benchmarks": ["each metric that will be measured"],
  "success_criteria": ["concrete stop/target thresholds"],
  "risks": ["safety or resource risks worth flagging to the user"]
}

Rules:
- Leave file "content" empty; it is generated after approval.
- Keep libraries realistic and minimal.
- If the brief describes an iterative/evolutionary loop, include an orchestrator
  file that runs the loop and logs to benchmark_history.json.
- Always include the risks a reviewer should see (e.g. spawning containers).
"""


def analyze(prompt: str, model: str | None = None) -> Plan:
    raw = chat_json(_SYSTEM, prompt, model=model)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Architect returned non-JSON output: {e}\n---\n{raw[:800]}")
    return Plan.model_validate(data)
