"""Ollama chat client with two interchangeable tool transports.

"native"  -> Ollama's /api/chat `tools` parameter (works when the model's
             renderer/parser pipeline is correct in the installed Ollama).
"json"    -> tool schemas are rendered into the system prompt and the model
             replies with a fenced JSON block we parse ourselves. Immune to
             renderer bugs; the safe default.

Both transports normalize to: {"content": str, "tool_calls": [{"name", "arguments"}]}
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx

from .config import OLLAMA_URL, config

JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class OllamaError(RuntimeError):
    pass


def _json_tool_instructions(tools: list[dict]) -> str:
    lines = [
        "You can call tools. Available tools (JSON schemas):",
        json.dumps(tools, indent=2),
        "",
        "To call a tool, reply with ONLY a fenced JSON block of the form:",
        '```json',
        '{"tool_call": {"name": "<tool name>", "arguments": {<args>}}}',
        '```',
        "One tool call per reply. After you receive the tool result you may call",
        "another tool or give your final answer. When you are NOT calling a tool,",
        "reply normally without any tool_call JSON.",
    ]
    return "\n".join(lines)


def extract_json_object(text: str) -> Optional[dict]:
    """Best-effort extraction of a JSON object from model output.

    Tries fenced ```json blocks first, then the largest brace-balanced span.
    """
    for m in JSON_BLOCK_RE.finditer(text):
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            c = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == '"':
                    in_string = False
                continue
            if c == '"':
                in_string = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


class OllamaClient:
    def __init__(self, base_url: str = OLLAMA_URL):
        self.base_url = base_url
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0))

    async def close(self) -> None:
        await self._client.aclose()

    async def _chat_raw(self, payload: dict) -> dict:
        r = await self._client.post(f"{self.base_url}/api/chat", json=payload)
        if r.status_code != 200:
            raise OllamaError(f"ollama /api/chat {r.status_code}: {r.text[:500]}")
        return r.json()

    async def chat(
        self,
        model: str,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        think: bool = False,
        options: Optional[dict] = None,
    ) -> dict:
        transport = config.get("model.tool_transport", "json")
        options = options or config.get("model.options", {})

        if tools and transport == "json":
            messages = list(messages)
            # Append tool instructions to the system message (or prepend one).
            instr = _json_tool_instructions(tools)
            if messages and messages[0].get("role") == "system":
                messages[0] = {
                    "role": "system",
                    "content": messages[0]["content"] + "\n\n" + instr,
                }
            else:
                messages.insert(0, {"role": "system", "content": instr})
            tools_param = None
        else:
            tools_param = tools

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": options,
        }
        if think:
            payload["think"] = True
        if tools_param:
            payload["tools"] = tools_param

        data = await self._chat_raw(payload)
        msg = data.get("message", {}) or {}
        content = msg.get("content", "") or ""
        tool_calls: list[dict] = []

        if tools and transport == "json":
            obj = extract_json_object(content)
            if obj and isinstance(obj.get("tool_call"), dict):
                tc = obj["tool_call"]
                if isinstance(tc.get("name"), str):
                    tool_calls.append({
                        "name": tc["name"],
                        "arguments": tc.get("arguments") or {},
                    })
                    content = ""
        else:
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {}) or {}
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                tool_calls.append({"name": fn.get("name", ""), "arguments": args})

        return {"content": content, "tool_calls": tool_calls, "raw": data}

    def tool_result_message(self, name: str, result: str) -> dict:
        """Format a tool result for the conversation, per active transport."""
        transport = config.get("model.tool_transport", "json")
        if transport == "json":
            return {"role": "user", "content": f"Tool result for {name}:\n{result}"}
        return {"role": "tool", "tool_name": name, "content": result}

    async def embed(self, text: str) -> Optional[list[float]]:
        model = config.get("model.embed_model")
        if not model:
            return None
        try:
            r = await self._client.post(
                f"{self.base_url}/api/embed",
                json={"model": model, "input": text[:8000]},
            )
            if r.status_code != 200:
                return None
            embs = r.json().get("embeddings") or []
            return embs[0] if embs else None
        except Exception:
            return None

    async def list_models(self) -> list[str]:
        try:
            r = await self._client.get(f"{self.base_url}/api/tags")
            return [m["name"] for m in r.json().get("models", [])]
        except Exception:
            return []


ollama = OllamaClient()
