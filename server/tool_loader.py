"""Hot-loading tool registry.

A tool is a .py file in $AILAB_HOME/tools exposing:
  SCHEMA  - OpenAI-style function schema dict
  execute - callable taking the schema's parameters as kwargs

Files are (re)loaded on startup, on mtime change (polled), and on demand via
POST /api/tools/reload. Broken files surface as error cards, never crash the
loader. Execution runs in a worker thread with a timeout, and results are
truncated before entering model context.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import importlib.util
import json
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .config import TOOLS_DIR, config


@dataclass
class LoadedTool:
    name: str
    path: str
    schema: Optional[dict] = None
    execute: Any = None
    status: str = "loaded"          # loaded | error
    error: Optional[str] = None
    mtime: float = 0.0


@dataclass
class ToolRegistry:
    tools: dict[str, LoadedTool] = field(default_factory=dict)
    _executor: concurrent.futures.ThreadPoolExecutor = field(
        default_factory=lambda: concurrent.futures.ThreadPoolExecutor(max_workers=4)
    )

    def _validate_schema(self, schema: Any) -> str:
        if not isinstance(schema, dict):
            return "SCHEMA must be a dict"
        if schema.get("type") != "function":
            return 'SCHEMA["type"] must be "function"'
        fn = schema.get("function")
        if not isinstance(fn, dict) or not isinstance(fn.get("name"), str):
            return 'SCHEMA["function"]["name"] missing'
        if not isinstance(fn.get("parameters"), dict):
            return 'SCHEMA["function"]["parameters"] missing'
        return ""

    def _load_file(self, path: Path) -> LoadedTool:
        name = path.stem
        tool = LoadedTool(name=name, path=str(path), mtime=path.stat().st_mtime)
        try:
            spec = importlib.util.spec_from_file_location(f"ailab_tool_{name}", path)
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            schema = getattr(module, "SCHEMA", None)
            err = self._validate_schema(schema)
            if err:
                raise ValueError(err)
            execute = getattr(module, "execute", None)
            if not callable(execute):
                raise ValueError("missing callable execute()")

            fn_name = schema["function"]["name"]
            tool.name = fn_name
            tool.schema = schema
            tool.execute = execute
        except Exception as e:
            tool.status = "error"
            tool.error = f"{type(e).__name__}: {e}"
            tool.execute = None
        return tool

    def reload(self) -> list[LoadedTool]:
        TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        found: dict[str, LoadedTool] = {}
        for path in sorted(TOOLS_DIR.glob("*.py")):
            if path.name.startswith("_"):
                continue
            tool = self._load_file(path)
            found[tool.name] = tool
        self.tools = found
        return list(found.values())

    def maybe_reload(self) -> bool:
        """Reload if any file changed/appeared/disappeared. Returns True on change."""
        try:
            current = {
                p.stem: p.stat().st_mtime
                for p in TOOLS_DIR.glob("*.py") if not p.name.startswith("_")
            }
        except FileNotFoundError:
            current = {}
        known = {Path(t.path).stem: t.mtime for t in self.tools.values()}
        if current != known:
            self.reload()
            return True
        return False

    def schemas(self) -> list[dict]:
        return [t.schema for t in self.tools.values() if t.status == "loaded" and t.schema]

    def summary(self) -> list[dict]:
        return [
            {"name": t.name, "path": t.path, "schema": t.schema,
             "status": t.status, "error": t.error}
            for t in self.tools.values()
        ]

    async def run(self, name: str, args: dict) -> str:
        """Execute a tool; always returns a string safe for model context."""
        max_chars = int(config.get("limits.tool_result_max_chars", 8000))
        timeout = int(config.get("limits.tool_timeout_seconds", 60))

        tool = self.tools.get(name)
        if tool is None or tool.status != "loaded":
            return json.dumps({"success": False, "error": f"unknown or unloaded tool: {name}"})

        loop = asyncio.get_running_loop()

        def _call() -> Any:
            return tool.execute(**args)

        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(self._executor, _call), timeout=timeout
            )
        except asyncio.TimeoutError:
            return json.dumps({"success": False, "error": f"tool timed out after {timeout}s"})
        except TypeError as e:
            return json.dumps({"success": False, "error": f"bad arguments: {e}"})
        except Exception:
            return json.dumps({"success": False,
                               "error": traceback.format_exc(limit=3)})

        try:
            text = result if isinstance(result, str) else json.dumps(result, default=str)
        except Exception:
            text = str(result)
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n...[truncated, {len(text)} chars total]"
        return text


registry = ToolRegistry()
