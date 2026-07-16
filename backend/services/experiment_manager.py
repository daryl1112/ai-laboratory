"""Owns the experiment lifecycle and ties the services together.

analyze()  -> architect model drafts a Plan (status: awaiting_approval)
approve()  -> codegen writes files, then a worker thread builds the image,
              runs the sandboxed container, and streams classified events
              onto the log bus while updating the persisted record.
stop()     -> kills the container.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from config import settings
from db import Store
from schemas import Experiment, Metric, Artifact, Status, WSMessage
from services import codegen, docker_manager, planner
from services.artifact_detector import classify
from services.log_bus import bus


class ExperimentManager:
    def __init__(self, store: Store):
        self.store = store

    # ---- helpers -----------------------------------------------------------
    def _touch(self, exp: Experiment) -> None:
        exp.updated_at = datetime.now(timezone.utc).isoformat()
        self.store.upsert(exp)

    def _emit(self, exp_id: str, msg: WSMessage) -> None:
        bus.publish(exp_id, msg)

    def _set_status(self, exp: Experiment, status: Status, error: str | None = None) -> None:
        exp.status = status
        if error:
            exp.error = error
        self._touch(exp)
        self._emit(exp.id, WSMessage(type="status", data={"status": status.value, "error": error}))

    # ---- lifecycle ---------------------------------------------------------
    def analyze(self, prompt: str, model: str | None = None) -> Experiment:
        exp = Experiment(
            id=uuid.uuid4().hex[:8],
            prompt=prompt,
            status=Status.analyzing,
            model=model or settings.architect_model,
        )
        self.store.upsert(exp)
        try:
            exp.plan = planner.analyze(prompt, model=model)
            self._set_status(exp, Status.awaiting_approval)
        except Exception as e:
            self._set_status(exp, Status.failed, error=str(e))
        return exp

    def approve(self, exp_id: str) -> Experiment:
        exp = self.store.get(exp_id)
        if not exp:
            raise KeyError(exp_id)
        if not exp.plan:
            raise ValueError("Experiment has no plan to approve")
        if exp.status not in (Status.awaiting_approval, Status.failed):
            raise ValueError(f"Cannot approve from status {exp.status}")

        codegen.materialize(exp.id, exp.plan)
        self._set_status(exp, Status.building)
        threading.Thread(target=self._run_worker, args=(exp.id,), daemon=True).start()
        return exp

    def stop(self, exp_id: str) -> None:
        docker_manager.stop_container(exp_id)
        exp = self.store.get(exp_id)
        if exp and exp.status in (Status.running, Status.building):
            self._set_status(exp, Status.stopped)

    # ---- worker thread -----------------------------------------------------
    def _run_worker(self, exp_id: str) -> None:
        exp = self.store.get(exp_id)
        exp_dir = settings.experiments_dir / exp_id
        try:
            for line in docker_manager.build_image(exp_id, exp_dir):
                self._emit(exp_id, WSMessage(type="log", data={"line": line, "tag": "docker"}))

            host_dir = None
            if settings.host_experiments_dir:
                from pathlib import Path
                host_dir = Path(settings.host_experiments_dir) / exp_id
            container = docker_manager.run_container(exp_id, exp_dir, host_dir=host_dir)
            exp = self.store.get(exp_id)
            exp.container_id = container.id[:12]
            self._set_status(exp, Status.running)

            for raw in docker_manager.stream_logs(container):
                text = raw.decode("utf-8", errors="replace")
                for line in text.splitlines():
                    if not line:
                        continue
                    msg = classify(exp_id, line, exp_dir)
                    self._emit(exp_id, msg)
                    self._persist_event(exp_id, msg)

            result = container.wait()
            code = result.get("StatusCode", 0)
            exp = self.store.get(exp_id)
            self._set_status(exp, Status.completed if code == 0 else Status.failed,
                             error=None if code == 0 else f"container exited with code {code}")
        except Exception as e:
            exp = self.store.get(exp_id)
            if exp:
                self._set_status(exp, Status.failed, error=str(e))
        finally:
            docker_manager.stop_container(exp_id)

    def _persist_event(self, exp_id: str, msg: WSMessage) -> None:
        exp = self.store.get(exp_id)
        if not exp:
            return
        if msg.type == "artifact":
            exp.artifacts.append(Artifact.model_validate(msg.data))
        elif msg.type == "metric":
            exp.metrics.append(Metric.model_validate(msg.data))
        elif msg.type == "iteration":
            exp.iteration = int(msg.data.get("iteration", exp.iteration))
        else:
            return
        self._touch(exp)
