"""Host stats for the dashboard: CPU/RAM/disk via psutil, GPU via rocm-smi."""
from __future__ import annotations

import asyncio
import json
import shutil
from typing import Optional

import psutil


async def _rocm_stats() -> Optional[dict]:
    if not shutil.which("rocm-smi"):
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "rocm-smi", "--showmeminfo", "vram", "--showuse", "--json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        data = json.loads(out.decode() or "{}")
        for card in data.values():
            if not isinstance(card, dict):
                continue
            used = card.get("VRAM Total Used Memory (B)")
            total = card.get("VRAM Total Memory (B)")
            busy = card.get("GPU use (%)")
            if used is not None and total is not None:
                return {
                    "vram_used_bytes": int(used),
                    "vram_total_bytes": int(total),
                    "gpu_busy_pct": float(busy) if busy is not None else None,
                }
    except Exception:
        return None
    return None


async def system_stats() -> dict:
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    gpu = await _rocm_stats()
    return {
        "cpu_pct": psutil.cpu_percent(interval=None),
        "ram_used_bytes": vm.used,
        "ram_total_bytes": vm.total,
        "disk_free_bytes": disk.free,
        "disk_total_bytes": disk.total,
        "gpu": gpu,
    }
