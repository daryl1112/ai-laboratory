"""Pydantic contracts. The Plan and CheckinAction models are the two
structured interfaces between the orchestrator and the model."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class PlanFile(BaseModel):
    path: str
    content: str

    @field_validator("path")
    @classmethod
    def safe_path(cls, v: str) -> str:
        if v.startswith("/") or ".." in v.split("/"):
            raise ValueError("file paths must be relative and inside the workspace")
        return v


class PlanEnvironment(BaseModel):
    type: Literal["base", "custom_dockerfile"] = "base"
    requirements: list[str] = Field(default_factory=list)
    dockerfile: Optional[str] = None


class PlanService(BaseModel):
    kind: Literal["postgres", "redis"]
    persist: bool = False


class PlanResources(BaseModel):
    cpus: float = 4.0
    mem_gb: float = 16.0
    timeout_minutes: int = 240


class Plan(BaseModel):
    title: str
    objective: str
    success_criteria: str
    environment: PlanEnvironment = Field(default_factory=PlanEnvironment)
    files: list[PlanFile]
    services: list[PlanService] = Field(default_factory=list)
    resources: PlanResources = Field(default_factory=PlanResources)
    checkin_interval_minutes: Optional[int] = None
    progress_convention: bool = True

    @field_validator("files")
    @classmethod
    def must_have_entrypoint(cls, v: list[PlanFile]) -> list[PlanFile]:
        if not any(f.path == "main.py" for f in v):
            raise ValueError("plan must include a main.py entrypoint file")
        return v


class CheckinAction(BaseModel):
    action: Literal["continue", "abort", "revise", "conclude"]
    reasoning: str = ""
    revised_files: list[PlanFile] = Field(default_factory=list)
    notes_for_ui: str = ""


# ---- API bodies ------------------------------------------------------------

class NewExperiment(BaseModel):
    prompt: str
    review_before_run: Optional[bool] = None
    network_access: Optional[bool] = None
    keep_services: Optional[bool] = None
    model: Optional[str] = None            # override per-phase routing with one model
    timeout_minutes: Optional[int] = None
    checkin_interval_minutes: Optional[int] = None
    cpus: Optional[float] = None
    mem_gb: Optional[float] = None


class ChatBody(BaseModel):
    message: str


class ToolTestBody(BaseModel):
    args: dict = Field(default_factory=dict)


class ConfigPatch(BaseModel):
    patch: dict
