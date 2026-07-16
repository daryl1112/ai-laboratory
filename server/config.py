"""Configuration for the AI lab orchestrator.

Defaults live here; user overrides persist in $AILAB_HOME/config.json and are
editable at runtime via GET/PATCH /api/config (no restart needed).
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

AILAB_HOME = Path(os.environ.get("AILAB_HOME", "~/ailab-data")).expanduser()
EXPERIMENTS_DIR = AILAB_HOME / "experiments"
TOOLS_DIR = AILAB_HOME / "tools"
CONFIG_PATH = AILAB_HOME / "config.json"

DB_DSN = os.environ.get("AILAB_DB_DSN", "postgresql://ailab:ailab@127.0.0.1:5432/ailab")
OLLAMA_URL = os.environ.get("AILAB_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
HOST = os.environ.get("AILAB_HOST", "127.0.0.1")
PORT = int(os.environ.get("AILAB_PORT", "8080"))

BASE_IMAGE = "ailab-base:py312"
LABEL_KEY = "ailab.exp"
LABEL_MANAGED = "ailab.managed"

DEFAULTS: dict = {
    "model": {
        # Per-phase routing. Any phase may be overridden; "default" is the fallback.
        "default": "qwen3-coder:30b",
        "per_phase": {
            "plan": " qwen3:30b-a3b-thinking-2507-q4_K_M",
            "code": "qwen3-coder:30b",
            "checkin": "qwen3-coder:30b",
            "conclude": " qwen3:30b-a3b-thinking-2507-q4_K_M",
            "chat": " qwen3:30b-a3b-thinking-2507-q4_K_M",
        },
        # Thinking mode per phase (only honored by models that support it).
        "think": {"plan": True, "code": False, "checkin": False, "conclude": True, "chat": True},
        # "native" = Ollama tools API; "json" = schemas in prompt + parsed JSON reply.
        "tool_transport": "json",
        "embed_model": "nomic-embed-text",
        "options": {"temperature": 0.7},
    },
    "limits": {
        "max_concurrent_experiments": 2,
        "default_cpus": 4.0,
        "default_mem_gb": 16,
        "max_cpus": 12.0,
        "max_mem_gb": 64,
        "default_timeout_minutes": 240,
        "max_revisions": 5,
        "max_plan_tool_iterations": 12,
        "tool_timeout_seconds": 60,
        "tool_result_max_chars": 8000,
        "log_tail_lines": 200,
    },
    "checkin": {
        "interval_minutes": 5,
        "on_events": True,          # also wake the model on error patterns / exit
        "stall_minutes": 20,        # no progress marker for this long -> event check-in
    },
    "experiments": {
        "review_before_run": True,
        "network_access": True,
        "keep_images": True,        # keep custom-built images while the experiment exists
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config:
    def __init__(self) -> None:
        self._overrides: dict = {}
        self.reload()

    def reload(self) -> None:
        if CONFIG_PATH.exists():
            try:
                self._overrides = json.loads(CONFIG_PATH.read_text())
            except Exception:
                self._overrides = {}
        else:
            self._overrides = {}

    @property
    def data(self) -> dict:
        return _deep_merge(DEFAULTS, self._overrides)

    def get(self, dotted: str, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def patch(self, patch: dict) -> dict:
        self._overrides = _deep_merge(self._overrides, patch)
        AILAB_HOME.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(self._overrides, indent=2))
        return self.data

    def model_for(self, phase: str) -> str:
        return self.get(f"model.per_phase.{phase}") or self.get("model.default")

    def think_for(self, phase: str) -> bool:
        return bool(self.get(f"model.think.{phase}", False))


config = Config()


def ensure_dirs() -> None:
    for d in (AILAB_HOME, EXPERIMENTS_DIR, TOOLS_DIR):
        d.mkdir(parents=True, exist_ok=True)
