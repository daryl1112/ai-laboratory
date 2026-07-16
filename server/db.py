"""asyncpg pool + helpers. Schema is applied idempotently on startup."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import asyncpg

from .config import DB_DSN

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

pool: Optional[asyncpg.Pool] = None


async def init() -> None:
    global pool
    pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=8)
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_PATH.read_text())
        await _register_json(conn)


async def _register_json(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    await conn.set_type_codec(
        "json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def _conn():
    assert pool is not None, "db not initialized"
    conn = await pool.acquire()
    try:
        await _register_json(conn)
    except Exception:
        pass
    return conn


async def fetch(query: str, *args) -> list[dict]:
    conn = await _conn()
    try:
        rows = await conn.fetch(query, *args)
        return [dict(r) for r in rows]
    finally:
        await pool.release(conn)


async def fetchrow(query: str, *args) -> Optional[dict]:
    conn = await _conn()
    try:
        row = await conn.fetchrow(query, *args)
        return dict(row) if row else None
    finally:
        await pool.release(conn)


async def execute(query: str, *args) -> str:
    conn = await _conn()
    try:
        return await conn.execute(query, *args)
    finally:
        await pool.release(conn)


# ---- experiments -----------------------------------------------------------

async def create_experiment(exp_id: str, prompt: str, options: dict) -> dict:
    return await fetchrow(
        """INSERT INTO experiments (id, prompt, options) VALUES ($1, $2, $3)
           RETURNING *""",
        exp_id, prompt, options,
    )


async def get_experiment(exp_id: str) -> Optional[dict]:
    return await fetchrow("SELECT * FROM experiments WHERE id = $1", exp_id)


async def list_experiments(limit: int = 100) -> list[dict]:
    return await fetch(
        """SELECT id, title, prompt, status, revision, progress_pct, progress_msg,
                  image, options, exit_code, created_at, started_at, finished_at
           FROM experiments ORDER BY created_at DESC LIMIT $1""",
        limit,
    )


async def update_experiment(exp_id: str, **fields: Any) -> None:
    if not fields:
        return
    cols, vals = [], []
    for i, (k, v) in enumerate(fields.items(), start=2):
        cols.append(f"{k} = ${i}")
        vals.append(v)
    await execute(f"UPDATE experiments SET {', '.join(cols)} WHERE id = $1", exp_id, *vals)


async def set_status(exp_id: str, status: str) -> None:
    await update_experiment(exp_id, status=status)
    await add_event(exp_id, "status", {"status": status})


async def delete_experiment(exp_id: str) -> None:
    await execute("DELETE FROM experiments WHERE id = $1", exp_id)


# ---- events / trace --------------------------------------------------------

async def add_event(exp_id: str, type_: str, payload: dict) -> dict:
    return await fetchrow(
        """INSERT INTO experiment_events (experiment_id, type, payload)
           VALUES ($1, $2, $3) RETURNING id, ts, type, payload""",
        exp_id, type_, payload,
    )


async def list_events(exp_id: str, limit: int = 500) -> list[dict]:
    return await fetch(
        """SELECT id, ts, type, payload FROM experiment_events
           WHERE experiment_id = $1 ORDER BY id ASC LIMIT $2""",
        exp_id, limit,
    )


# ---- messages --------------------------------------------------------------

async def add_message(exp_id: str, phase: str, role: str, content: str,
                      tool_calls: Optional[list] = None,
                      model: Optional[str] = None, think: Optional[bool] = None) -> None:
    await execute(
        """INSERT INTO messages (experiment_id, seq, phase, role, content, tool_calls, model, think)
           VALUES ($1,
                   COALESCE((SELECT MAX(seq) FROM messages WHERE experiment_id = $1), 0) + 1,
                   $2, $3, $4, $5, $6, $7)""",
        exp_id, phase, role, content, tool_calls, model, think,
    )


async def list_messages(exp_id: str) -> list[dict]:
    return await fetch(
        """SELECT seq, phase, role, content, tool_calls, model, think, created_at
           FROM messages WHERE experiment_id = $1 ORDER BY seq ASC""",
        exp_id,
    )


# ---- artifacts -------------------------------------------------------------

async def upsert_artifact(exp_id: str, path: str, size_bytes: int) -> bool:
    """Returns True if this artifact is new."""
    row = await fetchrow(
        """INSERT INTO artifacts (experiment_id, path, size_bytes)
           VALUES ($1, $2, $3)
           ON CONFLICT (experiment_id, path)
           DO UPDATE SET size_bytes = EXCLUDED.size_bytes
           RETURNING (xmax = 0) AS inserted""",
        exp_id, path, size_bytes,
    )
    return bool(row and row.get("inserted"))


async def list_artifacts(exp_id: str) -> list[dict]:
    return await fetch(
        "SELECT path, size_bytes, created_at FROM artifacts WHERE experiment_id = $1 ORDER BY path",
        exp_id,
    )


# ---- services --------------------------------------------------------------

async def add_service(exp_id: str, kind: str, container_name: str,
                      volume: Optional[str], persist: bool) -> None:
    await execute(
        """INSERT INTO services (experiment_id, kind, container_name, volume, persist, state)
           VALUES ($1, $2, $3, $4, $5, 'running')""",
        exp_id, kind, container_name, volume, persist,
    )


async def list_services(exp_id: Optional[str] = None) -> list[dict]:
    if exp_id:
        return await fetch("SELECT * FROM services WHERE experiment_id = $1 ORDER BY id", exp_id)
    return await fetch("SELECT * FROM services ORDER BY id DESC")


async def set_service_state(service_id: int, state: str) -> None:
    await execute("UPDATE services SET state = $2 WHERE id = $1", service_id, state)


async def delete_service(service_id: int) -> None:
    await execute("DELETE FROM services WHERE id = $1", service_id)


# ---- tools registry --------------------------------------------------------

async def upsert_tool(name: str, path: str, schema: Optional[dict],
                      status: str, error: Optional[str]) -> None:
    await execute(
        """INSERT INTO tools_registry (name, path, schema, status, error, loaded_at)
           VALUES ($1, $2, $3, $4, $5, now())
           ON CONFLICT (name) DO UPDATE
           SET path = $2, schema = $3, status = $4, error = $5, loaded_at = now()""",
        name, path, schema, status, error,
    )


async def clear_tools() -> None:
    await execute("DELETE FROM tools_registry")


# ---- embeddings / similarity -----------------------------------------------

async def set_conclusion(exp_id: str, conclusion: str, embedding: Optional[list[float]]) -> None:
    if embedding and len(embedding) == 768:
        vec = "[" + ",".join(f"{x:.7f}" for x in embedding) + "]"
        await execute(
            "UPDATE experiments SET conclusion = $2, conclusion_embedding = $3::vector WHERE id = $1",
            exp_id, conclusion, vec,
        )
    else:
        await update_experiment(exp_id, conclusion=conclusion)


async def similar_experiments(exp_id: str, limit: int = 5) -> list[dict]:
    return await fetch(
        """SELECT e.id, e.title, e.status, e.conclusion,
                  1 - (e.conclusion_embedding <=> me.conclusion_embedding) AS similarity
           FROM experiments e,
                (SELECT conclusion_embedding FROM experiments WHERE id = $1) me
           WHERE e.id <> $1
             AND e.conclusion_embedding IS NOT NULL
             AND me.conclusion_embedding IS NOT NULL
           ORDER BY e.conclusion_embedding <=> me.conclusion_embedding
           LIMIT $2""",
        exp_id, limit,
    )
