"""Central configuration. Reads from environment with safe defaults.

Nothing here imports docker or ollama, so the app can boot for inspection
even when those engines aren't installed yet.
"""
from __future__ import annotations

import os
from pathlib import Path


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _bool(key: str, default: bool) -> bool:
    return os.environ.get(key, str(default)).strip().lower() in {"1", "true", "yes", "on"}


BASE_DIR = Path(__file__).resolve().parent


class Settings:
    # Ollama
    ollama_host: str = _env("OLLAMA_HOST", "http://localhost:11434")
    architect_model: str = _env("ARCHITECT_MODEL", "qwen2.5-coder:7b")

    # Paths
    experiments_dir: Path = (BASE_DIR / _env("EXPERIMENTS_DIR", "../experiments")).resolve()
    db_path: Path = (BASE_DIR / _env("DB_PATH", "./lab.db")).resolve()
    # When the backend itself runs inside a container and creates *sibling*
    # experiment containers on the host daemon, the volume bind source must be a
    # HOST path, not the backend container's path. Set this to the host path that
    # maps to experiments_dir. Empty = backend runs on the host (paths match).
    host_experiments_dir: str = _env("HOST_EXPERIMENTS_DIR", "")

    # Generate real experiment code with the coder model on approval.
    # Falls back to a deterministic demo orchestrator when off or on failure.
    llm_codegen: bool = _bool("LLM_CODEGEN", True)

    # Sandbox limits
    container_memory: str = _env("CONTAINER_MEMORY", "4g")
    container_cpus: float = float(_env("CONTAINER_CPUS", "2"))
    container_pids_limit: int = int(_env("CONTAINER_PIDS_LIMIT", "512"))
    container_timeout_seconds: int = int(_env("CONTAINER_TIMEOUT_SECONDS", "21600"))
    container_network: str = _env("CONTAINER_NETWORK", "none")
    allow_nested_docker: bool = _bool("ALLOW_NESTED_DOCKER", False)

    # Restricted-egress proxy (see egress/). Only used for network=restricted.
    egress_internal_network: str = _env("EGRESS_INTERNAL_NETWORK", "ai-lab-egress-internal")
    egress_proxy_url: str = _env("EGRESS_PROXY_URL", "http://egress-proxy:8888")
    egress_proxy_container: str = _env("EGRESS_PROXY_CONTAINER", "ai-lab-egress-proxy")
    # Shared filter file the backend writes and the proxy reads. Empty disables
    # dynamic allowlist seeding (e.g. local dev without compose).
    egress_filter_path: str = _env("EGRESS_FILTER_PATH", "")

    # API
    cors_origins: list[str] = [
        o.strip() for o in _env("CORS_ORIGINS", "http://10.0.0.90:3000").split(",") if o.strip()
    ]

    def ensure_dirs(self) -> None:
        self.experiments_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
