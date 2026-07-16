"""Experiment lifecycle orchestration.

State machine:
  queued -> planning -> awaiting_approval -> provisioning -> running
         -> (paused) -> completed | failed | stopped
Revisions loop provisioning/running without leaving 'running' status.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import agent, db
from .config import BASE_IMAGE, EXPERIMENTS_DIR, config
from .docker_manager import get_docker
from .models import Plan, PlanFile
from .ws import hub

PROGRESS_RE = re.compile(r"##PROGRESS\s+(\d{1,3})\s*(.*?)##")

TERMINAL = {"completed", "failed", "stopped"}


class ExperimentRun:
    """In-memory handle for one active experiment task."""

    def __init__(self, exp_id: str):
        self.exp_id = exp_id
        self.task: Optional[asyncio.Task] = None
        self.container_id: Optional[str] = None
        self.stop_requested = False
        self.approval_event = asyncio.Event()
        self.approved = False
        self.wake_checkin = asyncio.Event()
        self.paused = False
        self.last_progress_ts = time.monotonic()


class Orchestrator:
    def __init__(self) -> None:
        self.active: dict[str, ExperimentRun] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._base_image_building = False

    # ---- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        n = int(config.get("limits.max_concurrent_experiments", 2))
        for _ in range(max(1, n)):
            self._workers.append(asyncio.create_task(self._worker()))
        asyncio.create_task(self._ensure_base_image())
        await self._recover()

    async def _recover(self) -> None:
        """After a restart: mark experiments that were mid-flight as failed
        (their in-memory tasks are gone) but leave their containers for audit."""
        rows = await db.fetch(
            "SELECT id, status FROM experiments WHERE status NOT IN "
            "('completed','failed','stopped','queued','awaiting_approval')")
        for row in rows:
            await db.set_status(row["id"], "failed")
            await db.add_event(row["id"], "error",
                               {"message": "orchestrator restarted mid-run"})
        requeued = await db.fetch("SELECT id FROM experiments WHERE status = 'queued'")
        for row in requeued:
            self.active[row["id"]] = ExperimentRun(row["id"])
            await self.queue.put(row["id"])

    async def _ensure_base_image(self) -> None:
        dk = get_docker()
        if dk.base_image_exists() or self._base_image_building:
            return
        self._base_image_building = True
        try:
            await hub.broadcast("status", {"exp": None, "status": "building base image"})
            await asyncio.get_running_loop().run_in_executor(None, dk.build_base_image)
            await hub.broadcast("status", {"exp": None, "status": "base image ready"})
        except Exception as e:
            await hub.broadcast("status",
                                {"exp": None, "status": f"base image build failed: {e}"})
        finally:
            self._base_image_building = False

    # ---- public API --------------------------------------------------------

    async def create(self, prompt: str, options: dict) -> dict:
        exp_id = uuid.uuid4().hex[:8]
        exp = await db.create_experiment(exp_id, prompt, options)
        ws = self.workspace(exp_id)
        (ws / "logs").mkdir(parents=True, exist_ok=True)
        (ws / "output").mkdir(parents=True, exist_ok=True)
        await db.add_event(exp_id, "created", {"prompt": prompt[:500]})
        self.active[exp_id] = ExperimentRun(exp_id)
        await self.queue.put(exp_id)
        await hub.broadcast("status", {"exp": exp_id, "status": "queued"})
        return exp

    async def approve(self, exp_id: str) -> None:
        run = self.active.get(exp_id)
        if run:
            run.approved = True
            run.approval_event.set()

    async def reject(self, exp_id: str) -> None:
        run = self.active.get(exp_id)
        if run:
            run.approved = False
            run.approval_event.set()

    async def stop(self, exp_id: str) -> None:
        run = self.active.get(exp_id)
        if run:
            run.stop_requested = True
            run.wake_checkin.set()
            if run.container_id:
                await asyncio.get_running_loop().run_in_executor(
                    None, get_docker().stop, run.container_id)
        else:
            exp = await db.get_experiment(exp_id)
            if exp and exp["status"] not in TERMINAL:
                await self._set_status(exp_id, "stopped")

    async def pause(self, exp_id: str) -> None:
        run = self.active.get(exp_id)
        if run and run.container_id and not run.paused:
            await asyncio.get_running_loop().run_in_executor(
                None, get_docker().pause, run.container_id)
            run.paused = True
            await self._set_status(exp_id, "paused")

    async def resume(self, exp_id: str) -> None:
        run = self.active.get(exp_id)
        if run and run.container_id and run.paused:
            await asyncio.get_running_loop().run_in_executor(
                None, get_docker().unpause, run.container_id)
            run.paused = False
            await self._set_status(exp_id, "running")

    async def rerun(self, exp_id: str) -> Optional[dict]:
        exp = await db.get_experiment(exp_id)
        if not exp:
            return None
        return await self.create(exp["prompt"], exp["options"])

    async def delete(self, exp_id: str) -> None:
        await self.stop(exp_id)
        dk = get_docker()
        await asyncio.get_running_loop().run_in_executor(
            None, dk.remove_experiment_everything, exp_id)
        ws = self.workspace(exp_id)
        if ws.exists():
            shutil.rmtree(ws, ignore_errors=True)
        await db.delete_experiment(exp_id)
        self.active.pop(exp_id, None)

    def workspace(self, exp_id: str) -> Path:
        return EXPERIMENTS_DIR / exp_id

    # ---- worker loop --------------------------------------------------------

    async def _worker(self) -> None:
        while True:
            exp_id = await self.queue.get()
            run = self.active.get(exp_id)
            if run is None:
                continue
            run.task = asyncio.current_task()
            try:
                await self._run_experiment(run)
            except Exception as e:
                await db.add_event(exp_id, "error", {"message": f"orchestrator error: {e}"})
                await self._set_status(exp_id, "failed")
            finally:
                self.active.pop(exp_id, None)

    async def _set_status(self, exp_id: str, status: str) -> None:
        await db.set_status(exp_id, status)
        await hub.broadcast("status", {"exp": exp_id, "status": status})

    async def _run_experiment(self, run: ExperimentRun) -> None:
        exp_id = run.exp_id
        exp = await db.get_experiment(exp_id)
        options = exp["options"] or {}

        # -- planning ----------------------------------------------------
        await self._set_status(exp_id, "planning")
        plan = await agent.run_planning(exp_id, exp["prompt"], options)
        if run.stop_requested:
            return await self._set_status(exp_id, "stopped")
        if plan is None:
            return await self._set_status(exp_id, "failed")

        plan = self._clamp_plan(plan, options)
        await db.update_experiment(exp_id, plan=json.loads(plan.model_dump_json()),
                                   title=plan.title)
        (self.workspace(exp_id) / "plan.json").write_text(plan.model_dump_json(indent=2))

        # -- approval gate -------------------------------------------------
        review = options.get("review_before_run")
        if review is None:
            review = config.get("experiments.review_before_run", True)
        if review:
            await self._set_status(exp_id, "awaiting_approval")
            await hub.broadcast("plan_ready", {"exp": exp_id})
            await run.approval_event.wait()
            if not run.approved or run.stop_requested:
                return await self._set_status(exp_id, "stopped")

        # -- provision + run (revision loop) ------------------------------
        await self._set_status(exp_id, "provisioning")
        await db.update_experiment(exp_id, started_at=datetime.now(timezone.utc))

        try:
            image, service_env, network_name = await self._provision(run, plan, options)
        except Exception as e:
            await db.add_event(exp_id, "error", {"message": f"provisioning failed: {e}"})
            return await self._set_status(exp_id, "failed")

        self._write_files(exp_id, plan.files, revision=1)
        await db.update_experiment(exp_id, revision=1, image=image)

        max_revisions = int(config.get("limits.max_revisions", 5))
        revision = 1
        outcome = "failed"
        exit_code: Optional[int] = None

        while True:
            if run.stop_requested:
                outcome = "stopped"
                break
            exit_code = await self._run_revision(
                run, plan, options, image, service_env, network_name, revision)
            if run.stop_requested:
                outcome = "stopped"
                break

            # container exited -> final check-in decides conclude vs revise
            log_tail = self._log_tail(exp_id, revision)
            action = await agent.run_checkin(
                exp_id,
                objective=plan.objective, success_criteria=plan.success_criteria,
                elapsed_minutes=self._elapsed_minutes(exp),
                container_state="exited", exit_code=exit_code,
                log_tail=log_tail, revision=revision, max_revisions=max_revisions)
            await hub.broadcast("trace", {"exp": exp_id, "event": {
                "type": "checkin", "payload": {"action": action.action,
                                               "notes": action.notes_for_ui}}})

            if action.action == "revise" and action.revised_files and revision < max_revisions:
                revision += 1
                self._apply_revision(exp_id, action.revised_files, revision)
                await db.update_experiment(exp_id, revision=revision)
                await db.add_event(exp_id, "revision",
                                   {"revision": revision,
                                    "files": [f.path for f in action.revised_files]})
                continue

            outcome = "completed" if exit_code == 0 else "failed"
            break

        # -- conclude -------------------------------------------------------
        await self._scan_artifacts(exp_id)
        artifacts = [a["path"] for a in await db.list_artifacts(exp_id)]
        if outcome in ("completed", "failed"):
            try:
                await agent.run_conclusion(
                    exp_id, objective=plan.objective,
                    success_criteria=plan.success_criteria,
                    log_tail=self._log_tail(exp_id, revision),
                    artifacts=artifacts,
                    outcome=f"{outcome} (exit code {exit_code})")
            except Exception as e:
                await db.add_event(exp_id, "warning",
                                   {"message": f"conclusion failed: {e}"})

        await db.update_experiment(exp_id, exit_code=exit_code,
                                   finished_at=datetime.now(timezone.utc))
        await self._teardown(run, plan, options)
        await self._set_status(exp_id, outcome)

    # ---- provisioning helpers ---------------------------------------------

    def _clamp_plan(self, plan: Plan, options: dict) -> Plan:
        lim = config.get("limits")
        plan.resources.cpus = min(
            options.get("cpus") or plan.resources.cpus or lim["default_cpus"],
            lim["max_cpus"])
        plan.resources.mem_gb = min(
            options.get("mem_gb") or plan.resources.mem_gb or lim["default_mem_gb"],
            lim["max_mem_gb"])
        plan.resources.timeout_minutes = int(
            options.get("timeout_minutes") or plan.resources.timeout_minutes
            or lim["default_timeout_minutes"])
        if not plan.checkin_interval_minutes:
            plan.checkin_interval_minutes = int(
                options.get("checkin_interval_minutes")
                or config.get("checkin.interval_minutes", 5))
        return plan

    async def _provision(self, run: ExperimentRun, plan: Plan,
                         options: dict) -> tuple[str, dict, Optional[str]]:
        exp_id = run.exp_id
        dk = get_docker()
        loop = asyncio.get_running_loop()
        ws = self.workspace(exp_id)

        service_env: dict = {}
        network_name: Optional[str] = None
        keep = options.get("keep_services")
        for svc in plan.services:
            persist = keep if keep is not None else svc.persist
            name, volume, env = await loop.run_in_executor(
                None, dk.start_service, exp_id, svc.kind)
            service_env.update(env)
            network_name = f"ailab-exp-{exp_id}"
            await db.add_service(exp_id, svc.kind, name, volume, bool(persist))
            await db.add_event(exp_id, "service", {"kind": svc.kind, "name": name})

        if plan.environment.type == "custom_dockerfile":
            dockerfile = plan.environment.dockerfile or ""
            (ws / "Dockerfile").write_text(dockerfile)
            self._write_files(exp_id, plan.files, revision=1)  # build context needs them
            image = await loop.run_in_executor(None, dk.build_custom_image, exp_id, ws)
        else:
            if not dk.base_image_exists():
                await self._ensure_base_image()
            image = BASE_IMAGE
            reqs = "\n".join(plan.environment.requirements)
            (ws / "requirements.txt").write_text(reqs + ("\n" if reqs else ""))

        return image, service_env, network_name

    def _write_files(self, exp_id: str, files: list[PlanFile], revision: int) -> None:
        ws = self.workspace(exp_id)
        snap = ws / "revisions" / f"r{revision}"
        snap.mkdir(parents=True, exist_ok=True)
        for f in files:
            for base in (ws, snap):
                target = base / f.path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f.content)

    def _apply_revision(self, exp_id: str, files: list[PlanFile], revision: int) -> None:
        self._write_files(exp_id, files, revision)

    # ---- run loop ------------------------------------------------------------

    async def _run_revision(self, run: ExperimentRun, plan: Plan, options: dict,
                            image: str, service_env: dict,
                            network_name: Optional[str], revision: int) -> Optional[int]:
        exp_id = run.exp_id
        dk = get_docker()
        loop = asyncio.get_running_loop()
        ws = self.workspace(exp_id)

        network_access = options.get("network_access")
        if network_access is None:
            network_access = config.get("experiments.network_access", True)

        container_id = await loop.run_in_executor(None, lambda: dk.run_experiment_container(
            exp_id, ws, image,
            cpus=plan.resources.cpus, mem_gb=plan.resources.mem_gb,
            network_access=bool(network_access), service_env=service_env,
            install_requirements=(image == BASE_IMAGE),
            network_name=network_name))
        run.container_id = container_id
        await db.update_experiment(exp_id, container_id=container_id)
        await self._set_status(exp_id, "running")
        await db.add_event(exp_id, "run", {"revision": revision, "container": container_id[:12]})

        log_path = ws / "logs" / f"run-r{revision}.log"
        run.last_progress_ts = time.monotonic()

        log_task = asyncio.create_task(
            self._pump_logs(run, container_id, log_path, revision))
        checkin_task = asyncio.create_task(
            self._checkin_loop(run, plan, revision))
        timeout_s = plan.resources.timeout_minutes * 60

        try:
            await asyncio.wait_for(log_task, timeout=timeout_s)
        except asyncio.TimeoutError:
            await db.add_event(exp_id, "warning",
                               {"message": f"timeout after {plan.resources.timeout_minutes} min"})
            await loop.run_in_executor(None, dk.stop, container_id)
            try:
                await asyncio.wait_for(log_task, timeout=30)
            except asyncio.TimeoutError:
                log_task.cancel()
        finally:
            checkin_task.cancel()

        status = await loop.run_in_executor(None, dk.status, container_id)
        run.container_id = None
        return (status or {}).get("exit_code")

    async def _pump_logs(self, run: ExperimentRun, container_id: str,
                         log_path: Path, revision: int) -> None:
        exp_id = run.exp_id
        dk = get_docker()
        with log_path.open("a") as fh:
            async for line in dk.stream_logs(container_id):
                fh.write(line + "\n")
                fh.flush()
                await hub.broadcast("log", {"exp": exp_id, "stream": "stdout",
                                            "revision": revision, "line": line})
                m = PROGRESS_RE.search(line)
                if m:
                    pct = min(100, int(m.group(1)))
                    msg = m.group(2).strip()
                    run.last_progress_ts = time.monotonic()
                    await db.update_experiment(exp_id, progress_pct=pct, progress_msg=msg)
                    await hub.broadcast("progress", {"exp": exp_id, "pct": pct, "message": msg})

    async def _checkin_loop(self, run: ExperimentRun, plan: Plan, revision: int) -> None:
        """Periodic + event-triggered mid-run check-ins while the container runs."""
        exp_id = run.exp_id
        dk = get_docker()
        loop = asyncio.get_running_loop()
        interval = (plan.checkin_interval_minutes or 5) * 60
        stall_s = int(config.get("checkin.stall_minutes", 20)) * 60
        max_revisions = int(config.get("limits.max_revisions", 5))
        exp = await db.get_experiment(exp_id)

        while True:
            run.wake_checkin.clear()
            try:
                await asyncio.wait_for(run.wake_checkin.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            if run.stop_requested or run.container_id is None:
                return
            if run.paused:
                continue

            state = await loop.run_in_executor(None, dk.status, run.container_id)
            if state is None or state["status"] != "running":
                return  # exit handling happens in _run_experiment

            stalled = (time.monotonic() - run.last_progress_ts) > stall_s
            log_tail = self._log_tail(exp_id, revision)
            action = await agent.run_checkin(
                exp_id,
                objective=plan.objective, success_criteria=plan.success_criteria,
                elapsed_minutes=self._elapsed_minutes(exp),
                container_state="running" + (" (stalled?)" if stalled else ""),
                exit_code=None, log_tail=log_tail,
                revision=revision, max_revisions=max_revisions)

            if action.notes_for_ui:
                await hub.broadcast("progress", {"exp": exp_id, "pct": None,
                                                 "message": action.notes_for_ui})
            if action.action == "abort":
                await db.add_event(exp_id, "warning",
                                   {"message": f"model aborted: {action.reasoning[:500]}"})
                await loop.run_in_executor(None, dk.stop, run.container_id)
                return
            if action.action == "revise" and action.revised_files:
                # Mid-run revise: stop the container; the exit path handles the
                # revise decision again with full context (files already sent
                # will be re-requested there for consistency).
                await loop.run_in_executor(None, dk.stop, run.container_id)
                return
            await self._scan_artifacts(exp_id)

    # ---- misc helpers -----------------------------------------------------

    def _log_tail(self, exp_id: str, revision: int) -> str:
        n = int(config.get("limits.log_tail_lines", 200))
        path = self.workspace(exp_id) / "logs" / f"run-r{revision}.log"
        if not path.exists():
            return ""
        lines = path.read_text(errors="replace").splitlines()
        return "\n".join(lines[-n:])

    def _elapsed_minutes(self, exp: dict) -> float:
        started = exp.get("started_at") or exp.get("created_at")
        if not started:
            return 0.0
        now = datetime.now(timezone.utc)
        return (now - started).total_seconds() / 60.0

    async def _scan_artifacts(self, exp_id: str) -> None:
        out = self.workspace(exp_id) / "output"
        if not out.exists():
            return
        for path in out.rglob("*"):
            if path.is_file():
                rel = str(path.relative_to(out))
                new = await db.upsert_artifact(exp_id, rel, path.stat().st_size)
                if new:
                    await hub.broadcast("trace", {"exp": exp_id, "event": {
                        "type": "artifact", "payload": {"path": rel}}})

    async def _teardown(self, run: ExperimentRun, plan: Plan, options: dict) -> None:
        exp_id = run.exp_id
        dk = get_docker()
        loop = asyncio.get_running_loop()
        services = await db.list_services(exp_id)
        persist_names = {s["container_name"] for s in services if s["persist"]}
        await loop.run_in_executor(None, lambda: dk.cleanup_experiment(
            exp_id, keep_persistent=bool(persist_names), persist_names=persist_names))
        for s in services:
            await db.set_service_state(
                s["id"], "stopped" if s["container_name"] in persist_names else "removed")
        keep_images = config.get("experiments.keep_images", True)
        if not keep_images and (await db.get_experiment(exp_id) or {}).get("image", "").startswith("ailab-exp-"):
            await loop.run_in_executor(None, dk.remove_experiment_everything, exp_id)


orchestrator = Orchestrator()
