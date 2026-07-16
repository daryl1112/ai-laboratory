"""Builds and runs experiment containers with a hardened, sandboxed profile.

The backend (trusted) talks to the host Docker daemon. The experiment container
NEVER receives the host Docker socket. Every run gets:
  - no network by default          (settings.container_network)
  - memory / cpu / pid caps
  - all Linux capabilities dropped + no-new-privileges
  - a read-only root filesystem with a small writable tmpfs
  - a hard timeout kill enforced by the caller

For experiments that must spawn their own containers, the safe path is a
rootless Docker-in-Docker sidecar or the sysbox runtime (ALLOW_NESTED_DOCKER),
never a bind-mount of /var/run/docker.sock.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from config import settings


class DockerUnavailable(RuntimeError):
    pass


def _client():
    try:
        import docker
    except ImportError as e:  # pragma: no cover
        raise DockerUnavailable("The 'docker' package is not installed. Run: pip install docker") from e
    try:
        return docker.from_env()
    except Exception as e:
        raise DockerUnavailable(f"Could not connect to the Docker daemon: {e}") from e


def build_image(exp_id: str, context_dir: Path) -> Iterator[str]:
    """Build the image, yielding human-readable build log lines."""
    client = _client()
    tag = f"ai-lab/{exp_id}:latest"
    # low-level API gives us a streaming build log
    api = client.api
    for chunk in api.build(path=str(context_dir), tag=tag, rm=True, decode=True):
        if "stream" in chunk:
            line = chunk["stream"].rstrip()
            if line:
                yield line
        elif "error" in chunk:
            raise DockerUnavailable(chunk["error"].strip())
    yield f"[docker] built image {tag}"


def run_container(exp_id: str, context_dir: Path, host_dir: Path | None = None,
                  network_policy: str = "none"):
    """Start the experiment container under the sandbox profile. Returns the container.

    host_dir is the bind source used when the backend runs inside a container and
    creates sibling containers on the host daemon (the mount source must be a host
    path). When None, context_dir is used (backend running directly on the host).

    network_policy controls runtime egress:
      none        -> --network none (fully isolated, default)
      restricted  -> internal network + HTTP(S)_PROXY to the allowlist proxy
      open        -> bridge network (full internet; use sparingly)
    """
    client = _client()
    tag = f"ai-lab/{exp_id}:latest"
    bind_source = str(host_dir or context_dir)

    env: dict[str, str] = {}
    net_kwargs: dict = {}
    if network_policy == "restricted":
        net_kwargs["network"] = settings.egress_internal_network
        env.update({
            "HTTP_PROXY": settings.egress_proxy_url,
            "HTTPS_PROXY": settings.egress_proxy_url,
            "http_proxy": settings.egress_proxy_url,
            "https_proxy": settings.egress_proxy_url,
            # never proxy same-host / metadata lookups
            "NO_PROXY": "localhost,127.0.0.1",
        })
    elif network_policy == "open":
        net_kwargs["network_mode"] = "bridge"
    else:  # none
        net_kwargs["network_mode"] = "none"

    kwargs = dict(
        name=f"ai-lab-{exp_id}",
        detach=True,
        mem_limit=settings.container_memory,
        nano_cpus=int(settings.container_cpus * 1_000_000_000),
        pids_limit=settings.container_pids_limit,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        read_only=True,
        tmpfs={"/tmp": "size=256m", "/experiment/artifacts": "size=512m"},
        working_dir="/experiment",
        environment=env,
        # host results are collected from the mounted experiment dir
        volumes={bind_source: {"bind": "/experiment", "mode": "rw"}},
        labels={"ai-lab.experiment": exp_id},
        **net_kwargs,
    )

    if settings.allow_nested_docker:
        # sysbox gives a real, unprivileged, isolated docker daemon inside the
        # container without the host socket. Requires the sysbox runtime installed.
        kwargs["runtime"] = "sysbox-runc"
        kwargs["read_only"] = False  # nested dockerd needs a writable root

    return client.containers.run(image=tag, **kwargs)


def stream_logs(container) -> Iterator[bytes]:
    """Yield raw log bytes as the container produces them."""
    return container.logs(stream=True, follow=True, stdout=True, stderr=True)


def stop_container(exp_id: str) -> None:
    client = _client()
    try:
        c = client.containers.get(f"ai-lab-{exp_id}")
        c.stop(timeout=5)
        c.remove(force=True)
    except Exception:
        pass  # already gone
