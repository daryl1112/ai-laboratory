"""Classifies raw container log lines into structured console events.

Recognized markers (printed by experiment code):
  ::artifact:: <path>                -> artifact event  (highlighted chip)
  ::metric:: <name> <value> [unit]   -> metric event    (updates live gauges)
  ::iteration:: <n>                  -> iteration divider
Bracket tags like [docker] [bench] [hypothesis] are surfaced as the line's
category so the UI can colorize them. Everything else is a plain log line.
"""
from __future__ import annotations

import re
from pathlib import Path

from schemas import Artifact, Metric, WSMessage

_ARTIFACT = re.compile(r"^::artifact::\s+(?P<path>.+)$")
_METRIC = re.compile(r"^::metric::\s+(?P<name>\S+)\s+(?P<value>[-\d.]+)\s*(?P<unit>\S*)$")
_ITER = re.compile(r"^::iteration::\s+(?P<n>\d+)$")
_TAG = re.compile(r"^\[(?P<tag>[a-zA-Z_-]+)\]")


def classify(exp_id: str, line: str, exp_dir: Path) -> WSMessage:
    line = line.rstrip("\n")

    m = _ARTIFACT.match(line)
    if m:
        rel = m.group("path").strip()
        size = 0
        try:
            size = (exp_dir / rel).stat().st_size
        except OSError:
            pass
        art = Artifact(path=rel, size_bytes=size)
        return WSMessage(type="artifact", data=art.model_dump())

    m = _METRIC.match(line)
    if m:
        metric = Metric(
            name=m.group("name"),
            value=float(m.group("value")),
            unit=m.group("unit") or "",
        )
        return WSMessage(type="metric", data=metric.model_dump())

    m = _ITER.match(line)
    if m:
        return WSMessage(type="iteration", data={"iteration": int(m.group("n"))})

    tag = None
    tm = _TAG.match(line)
    if tm:
        tag = tm.group("tag")
    return WSMessage(type="log", data={"line": line, "tag": tag})
