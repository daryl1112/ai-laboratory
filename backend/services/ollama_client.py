"""Wrapper around the Ollama Python client.

Imports lazily so the API still boots on a machine without Ollama installed;
the failure surfaces only when planning is actually requested.
"""
from __future__ import annotations

from config import settings


class OllamaUnavailable(RuntimeError):
    pass


def _client():
    try:
        import ollama
    except ImportError as e:  # pragma: no cover
        raise OllamaUnavailable(
            "The 'ollama' package is not installed. Run: pip install ollama"
        ) from e
    return ollama.Client(host=settings.ollama_host)


def chat_json(system: str, user: str, model: str | None = None) -> str:
    """Ask the model for a response, forcing JSON output format."""
    client = _client()
    try:
        resp = client.chat(
            model=model or settings.architect_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            format="json",
            options={"temperature": 0.2},
        )
    except Exception as e:  # connection refused, model not pulled, etc.
        raise OllamaUnavailable(
            f"Could not reach Ollama at {settings.ollama_host} "
            f"using model '{model or settings.architect_model}': {e}"
        ) from e
    return resp["message"]["content"]


def chat_text(system: str, user: str, model: str | None = None) -> str:
    """Free-form completion used for code generation."""
    client = _client()
    try:
        resp = client.chat(
            model=model or settings.architect_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            options={"temperature": 0.1},
        )
    except Exception as e:
        raise OllamaUnavailable(
            f"Could not reach Ollama at {settings.ollama_host}: {e}"
        ) from e
    return resp["message"]["content"]
