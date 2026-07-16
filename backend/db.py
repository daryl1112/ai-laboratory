"""Tiny SQLite store. Experiments are persisted as JSON blobs keyed by id.

Kept deliberately simple — swap for Postgres later without touching callers.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from schemas import Experiment

_lock = threading.Lock()


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS experiments ("
            "  id TEXT PRIMARY KEY,"
            "  created_at TEXT,"
            "  blob TEXT NOT NULL"
            ")"
        )
        self._conn.commit()

    def upsert(self, exp: Experiment) -> None:
        with _lock:
            self._conn.execute(
                "INSERT INTO experiments (id, created_at, blob) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET blob = excluded.blob",
                (exp.id, exp.created_at, exp.model_dump_json()),
            )
            self._conn.commit()

    def get(self, exp_id: str) -> Experiment | None:
        with _lock:
            row = self._conn.execute(
                "SELECT blob FROM experiments WHERE id = ?", (exp_id,)
            ).fetchone()
        if not row:
            return None
        return Experiment.model_validate_json(row[0])

    def list(self) -> list[Experiment]:
        with _lock:
            rows = self._conn.execute(
                "SELECT blob FROM experiments ORDER BY created_at DESC"
            ).fetchall()
        return [Experiment.model_validate_json(r[0]) for r in rows]
