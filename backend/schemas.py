"""Data models shared across the API and services.

The `Plan` schema is also the contract the architect model must return as JSON,
so keep it stable — planner.py embeds it in the system prompt.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Status(str, Enum):
    analyzing = "analyzing"            # architect model is drafting the plan
    awaiting_approval = "awaiting_approval"
    building = "building"              # docker image build in progress
    running = "running"
    completed = "completed"
    failed = "failed"
    stopped = "stopped"


class NetworkPolicy(str, Enum):
    none = "none"                      # --network none (default, fully isolated)
    restricted = "restricted"         # internal net + allowlist egress proxy
    open = "open"                     # full bridge internet (use sparingly)


class PlanFile(BaseModel):
    path: str = Field(..., description="Relative path inside the experiment folder")
    purpose: str = ""
    content: str = ""                 # may be empty; codegen can fill on approval


class DockerSpec(BaseModel):
    base_image: str = "python:3.12-slim"
    system_packages: list[str] = []
    entrypoint: list[str] = ["python", "orchestrator.py"]


class Plan(BaseModel):
    title: str
    objective: str
    summary: str
    language: str = "python"
    libraries: list[str] = []
    tools: list[str] = []
    files: list[PlanFile] = []
    docker: DockerSpec = DockerSpec()
    benchmarks: list[str] = []
    success_criteria: list[str] = []
    risks: list[str] = []
    # Runtime network access the experiment needs. Default-deny.
    network: NetworkPolicy = NetworkPolicy.none
    # Domains the experiment must reach at runtime (only used when network=restricted).
    network_allowlist: list[str] = []


class AnalyzeRequest(BaseModel):
    prompt: str
    model: str | None = None


class ApproveRequest(BaseModel):
    """Optional operator overrides applied at the approval gate."""
    network: NetworkPolicy | None = None
    network_allowlist: list[str] | None = None


class Metric(BaseModel):
    name: str
    value: float
    unit: str = ""
    iteration: int | None = None
    at: str = Field(default_factory=now_iso)


class Artifact(BaseModel):
    path: str
    size_bytes: int = 0
    kind: str = "file"
    at: str = Field(default_factory=now_iso)


class Experiment(BaseModel):
    id: str
    prompt: str
    status: Status
    plan: Plan | None = None
    model: str
    container_id: str | None = None
    metrics: list[Metric] = []
    artifacts: list[Artifact] = []
    iteration: int = 0
    error: str | None = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


# --- WebSocket message envelope ---------------------------------------------
class WSMessage(BaseModel):
    """Everything the UI receives on the experiment WebSocket."""
    type: str                         # log | artifact | metric | status | iteration
    data: dict[str, Any]
    at: str = Field(default_factory=now_iso)
