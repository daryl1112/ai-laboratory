"""Manages the restricted-egress allowlist.

When an experiment is approved with network=restricted, we take the domains its
plan declared, validate them, append any new ones to the proxy's shared filter
file, and reload the proxy (SIGHUP) so tinyproxy re-reads the allowlist.

Domains are converted to anchored host regexes so a listed domain also matches
its subdomains but nothing else. Validation rejects anything that isn't a plain
hostname, so a malicious plan can't inject arbitrary regex into the filter.
"""
from __future__ import annotations

import re
from pathlib import Path

from config import settings

# a conservative hostname: labels of alnum/hyphen separated by dots
_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$", re.I)


def _to_pattern(domain: str) -> str:
    esc = re.escape(domain.strip().lower())
    return rf"(^|\.){esc}$"


def valid_domain(domain: str) -> bool:
    return bool(_HOST_RE.match(domain.strip()))


def seed_allowlist(domains: list[str]) -> list[str]:
    """Add validated domains to the filter file and reload the proxy.

    Returns the list of domains that were accepted. No-ops (returns []) when no
    filter path is configured, e.g. local dev without the compose proxy.
    """
    accepted = [d.strip().lower() for d in domains if valid_domain(d)]
    if not settings.egress_filter_path or not accepted:
        return accepted

    path = Path(settings.egress_filter_path)
    try:
        existing = path.read_text().splitlines() if path.exists() else []
    except OSError:
        existing = []

    existing_set = {ln.strip() for ln in existing}
    new_lines = [p for d in accepted if (p := _to_pattern(d)) not in existing_set]
    if new_lines:
        with path.open("a") as f:
            f.write("\n" + "\n".join(new_lines) + "\n")
        _reload_proxy()
    return accepted


def _reload_proxy() -> None:
    try:
        import docker
    except ImportError:
        return
    try:
        client = docker.from_env()
        client.containers.get(settings.egress_proxy_container).kill(signal="SIGHUP")
    except Exception:
        # proxy not running (local dev) — the filter file is still updated for
        # whenever it does come up.
        pass
