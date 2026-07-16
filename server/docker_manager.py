"""Docker lifecycle for experiments.

Everything created for an experiment carries the label ailab.exp=<id> plus
ailab.managed=1, so cleanup and crash-recovery are label queries rather than
bookkeeping.
"""
from __future__ import annotations

import asyncio
import secrets
import shlex
from pathlib import Path
from typing import AsyncIterator, Optional

import docker
from docker.errors import APIError, ImageNotFound, NotFound

from .config import BASE_IMAGE, LABEL_KEY, LABEL_MANAGED

BASE_DOCKERFILE_DIR = Path(__file__).resolve().parent.parent / "base-image"

SERVICE_SPECS = {
    "postgres": {
        "image": "pgvector/pgvector:pg17",
        "port": 5432,
        "env": lambda pw: {"POSTGRES_DB": "experiment", "POSTGRES_USER": "experiment",
                           "POSTGRES_PASSWORD": pw},
        "volume_path": "/var/lib/postgresql/data",
        "conn_env": lambda host, pw: {
            "DATABASE_URL": f"postgresql://experiment:{pw}@{host}:5432/experiment",
            "PGHOST": host, "PGPORT": "5432", "PGUSER": "experiment",
            "PGPASSWORD": pw, "PGDATABASE": "experiment",
        },
    },
    "redis": {
        "image": "redis:7-alpine",
        "port": 6379,
        "env": lambda pw: {},
        "volume_path": "/data",
        "conn_env": lambda host, pw: {"REDIS_URL": f"redis://{host}:6379/0",
                                      "REDIS_HOST": host, "REDIS_PORT": "6379"},
    },
}


class DockerManager:
    def __init__(self) -> None:
        self.client = docker.from_env()

    # ---- images -------------------------------------------------------

    def base_image_exists(self) -> bool:
        try:
            self.client.images.get(BASE_IMAGE)
            return True
        except ImageNotFound:
            return False

    def build_base_image(self) -> None:
        """Blocking build of the fat base image (call in a thread)."""
        self.client.images.build(
            path=str(BASE_DOCKERFILE_DIR), tag=BASE_IMAGE, rm=True,
            labels={LABEL_MANAGED: "1"},
        )

    def build_custom_image(self, exp_id: str, workspace: Path) -> str:
        tag = f"ailab-exp-{exp_id}:latest"
        self.client.images.build(
            path=str(workspace), tag=tag, rm=True,
            labels={LABEL_KEY: exp_id, LABEL_MANAGED: "1"},
        )
        return tag

    def prune_experiment_images(self, keep_ids: set[str]) -> list[str]:
        removed = []
        for img in self.client.images.list(filters={"label": LABEL_MANAGED}):
            exp = (img.labels or {}).get(LABEL_KEY)
            if exp and exp not in keep_ids:
                try:
                    self.client.images.remove(img.id, force=True)
                    removed.append(exp)
                except APIError:
                    pass
        return removed

    # ---- services -------------------------------------------------------

    def create_network(self, exp_id: str) -> str:
        name = f"ailab-exp-{exp_id}"
        try:
            self.client.networks.get(name)
        except NotFound:
            self.client.networks.create(
                name, labels={LABEL_KEY: exp_id, LABEL_MANAGED: "1"})
        return name

    def start_service(self, exp_id: str, kind: str) -> tuple[str, Optional[str], dict]:
        """Start a service container on the experiment network.

        Returns (container_name, volume_name, env vars to inject into the
        experiment container)."""
        spec = SERVICE_SPECS[kind]
        network = self.create_network(exp_id)
        name = f"ailab-exp-{exp_id}-{kind}"
        volume = f"ailab-exp-{exp_id}-{kind}-data"
        password = secrets.token_urlsafe(12)

        self.client.volumes.create(
            volume, labels={LABEL_KEY: exp_id, LABEL_MANAGED: "1"})
        try:
            existing = self.client.containers.get(name)
            existing.remove(force=True)
        except NotFound:
            pass
        self.client.containers.run(
            spec["image"],
            name=name,
            detach=True,
            network=network,
            environment=spec["env"](password),
            volumes={volume: {"bind": spec["volume_path"], "mode": "rw"}},
            labels={LABEL_KEY: exp_id, LABEL_MANAGED: "1", "ailab.service": kind},
        )
        return name, volume, spec["conn_env"](name, password)

    # ---- experiment container -------------------------------------------

    def run_experiment_container(
        self,
        exp_id: str,
        workspace: Path,
        image: str,
        *,
        cpus: float,
        mem_gb: float,
        network_access: bool,
        service_env: dict,
        install_requirements: bool,
        network_name: Optional[str],
    ) -> str:
        name = f"ailab-exp-{exp_id}-run"
        try:
            old = self.client.containers.get(name)
            old.remove(force=True)
        except NotFound:
            pass

        if install_requirements:
            cmd = ("sh -c " + shlex.quote(
                "if [ -s requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi "
                "&& exec python -u main.py"))
        else:
            cmd = "python -u main.py"

        kwargs: dict = dict(
            image=image,
            name=name,
            command=cmd,
            detach=True,
            working_dir="/workspace",
            volumes={str(workspace): {"bind": "/workspace", "mode": "rw"}},
            environment={**service_env, "PYTHONUNBUFFERED": "1"},
            labels={LABEL_KEY: exp_id, LABEL_MANAGED: "1", "ailab.run": "1"},
            nano_cpus=int(cpus * 1e9),
            mem_limit=f"{int(mem_gb * 1024)}m",
        )
        if network_name:
            kwargs["network"] = network_name
        elif not network_access:
            kwargs["network_mode"] = "none"

        container = self.client.containers.run(**kwargs)
        return container.id

    # ---- container ops ---------------------------------------------------

    def _get(self, container_id: str):
        return self.client.containers.get(container_id)

    def stop(self, container_id: str, timeout: int = 10) -> None:
        try:
            self._get(container_id).stop(timeout=timeout)
        except NotFound:
            pass

    def pause(self, container_id: str) -> None:
        self._get(container_id).pause()

    def unpause(self, container_id: str) -> None:
        self._get(container_id).unpause()

    def remove(self, container_id: str) -> None:
        try:
            self._get(container_id).remove(force=True)
        except NotFound:
            pass

    def status(self, container_id: str) -> Optional[dict]:
        """Returns {'status': ..., 'exit_code': ...} or None if gone."""
        try:
            c = self._get(container_id)
            c.reload()
            return {
                "status": c.status,
                "exit_code": c.attrs.get("State", {}).get("ExitCode"),
            }
        except NotFound:
            return None

    def container_stats(self, container_id: str) -> Optional[dict]:
        try:
            c = self._get(container_id)
            s = c.stats(stream=False)
            cpu_delta = (s["cpu_stats"]["cpu_usage"]["total_usage"]
                         - s["precpu_stats"]["cpu_usage"]["total_usage"])
            sys_delta = (s["cpu_stats"].get("system_cpu_usage", 0)
                         - s["precpu_stats"].get("system_cpu_usage", 0))
            ncpus = s["cpu_stats"].get("online_cpus") or 1
            cpu_pct = (cpu_delta / sys_delta * ncpus * 100.0) if sys_delta > 0 else 0.0
            mem = s.get("memory_stats", {})
            return {
                "cpu_pct": round(cpu_pct, 1),
                "mem_bytes": mem.get("usage", 0),
                "mem_limit_bytes": mem.get("limit", 0),
            }
        except Exception:
            return None

    async def stream_logs(self, container_id: str) -> AsyncIterator[str]:
        """Async line iterator over a container's stdout+stderr."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Optional[str]] = asyncio.Queue(maxsize=1000)

        def _pump() -> None:
            try:
                c = self.client.containers.get(container_id)
                for chunk in c.logs(stream=True, follow=True, stdout=True, stderr=True):
                    line = chunk.decode("utf-8", errors="replace")
                    asyncio.run_coroutine_threadsafe(queue.put(line), loop).result()
            except Exception:
                pass
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

        loop.run_in_executor(None, _pump)
        buffer = ""
        while True:
            item = await queue.get()
            if item is None:
                if buffer:
                    yield buffer
                return
            buffer += item
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                yield line

    # ---- cleanup ---------------------------------------------------------

    def cleanup_experiment(self, exp_id: str, *, keep_persistent: bool,
                           persist_names: set[str]) -> None:
        """Remove (or stop-and-keep) everything labeled with this experiment."""
        flt = {"label": f"{LABEL_KEY}={exp_id}"}
        for c in self.client.containers.list(all=True, filters=flt):
            if keep_persistent and c.name in persist_names:
                try:
                    if c.status == "running":
                        c.stop(timeout=10)
                except APIError:
                    pass
            else:
                try:
                    c.remove(force=True)
                except APIError:
                    pass
        keep_volumes = keep_persistent and bool(persist_names)
        if not keep_volumes:
            for v in self.client.volumes.list(filters=flt):
                try:
                    v.remove(force=True)
                except APIError:
                    pass
            for n in self.client.networks.list(filters=flt):
                try:
                    n.remove()
                except APIError:
                    pass

    def remove_experiment_everything(self, exp_id: str) -> None:
        self.cleanup_experiment(exp_id, keep_persistent=False, persist_names=set())
        for img in self.client.images.list(filters={"label": f"{LABEL_KEY}={exp_id}"}):
            try:
                self.client.images.remove(img.id, force=True)
            except APIError:
                pass

    def service_container_op(self, container_name: str, op: str) -> None:
        c = self.client.containers.get(container_name)
        if op == "start":
            c.start()
        elif op == "stop":
            c.stop(timeout=10)
        elif op == "delete":
            c.remove(force=True)


dockerman: Optional[DockerManager] = None


def get_docker() -> DockerManager:
    global dockerman
    if dockerman is None:
        dockerman = DockerManager()
    return dockerman
