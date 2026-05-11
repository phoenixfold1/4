from __future__ import annotations

import asyncio
import time
import httpx

from llm.session_store import SessionStore
from logger_config import logger
from modules.critic.agent import CriticAgent
from modules.js_checker.module import JSCheckerModule
from modules.renderer.module import RendererModule
from modules.scene_coder.agent import SceneCoderAgent
from pipeline.bus import EventBus
from pipeline.events import (
    CheckerFailed,
    CheckerOk,
    CoderDone,
    CriticDone,
    Event,
    PatcherDone,
    RenderDone,
    TaskCreated,
    TaskDone,
    TaskFailed,
)
from pipeline.task import PipelineTask
from utils.http import download_image


def _stg(n: int, name: str) -> str:
    return f"[{n}/6 {name}]"


class Orchestrator:
    """Wires actors into the bus, enforces deadlines + iter caps, threads
    one PipelineTask through every stage."""

    def __init__(
        self,
        *,
        bus: EventBus,
        session_store: SessionStore,
        coder: SceneCoderAgent,
        critic: CriticAgent,
        js_checker: JSCheckerModule,
        renderer: RendererModule,
        http_client: httpx.AsyncClient,
        coder_multimodal: bool = False,
        task_deadline_s: float = 60.0,
        max_iter: int = 2,
        score_threshold: float = 0.80,
        coder_workers: int = 2,
        checker_workers: int = 2,
        renderer_workers: int = 1,
        critic_workers: int = 3,
        patcher_workers: int = 2,
        queue_size: int = 8,
    ) -> None:
        self.bus = bus
        self.session_store = session_store
        self.coder = coder
        self.critic = critic
        self.js_checker = js_checker
        self.renderer = renderer
        self.http_client = http_client
        self.coder_multimodal = coder_multimodal
        self.task_deadline_s = task_deadline_s
        self.max_iter = max_iter
        self.score_threshold = score_threshold
        self._tasks: dict[str, PipelineTask] = {}
        self._futures: dict[str, asyncio.Future[PipelineTask]] = {}

        self._wire_actors(
            coder_workers=coder_workers,
            checker_workers=checker_workers,
            renderer_workers=renderer_workers,
            critic_workers=critic_workers,
            patcher_workers=patcher_workers,
            queue_size=queue_size,
        )

    # Wiring

    def _wire_actors(
        self, *, coder_workers, checker_workers,
        renderer_workers, critic_workers, patcher_workers, queue_size,
    ) -> None:

        # Register actors
        self.bus.register_actor("coder",   self._on_coder_input,  workers=coder_workers,   queue_size=queue_size)
        self.bus.register_actor("checker", self._on_coder_done,   workers=checker_workers, queue_size=queue_size)
        self.bus.register_actor("renderer",self._on_checker_ok,   workers=renderer_workers,queue_size=queue_size)
        self.bus.register_actor("critic",  self._on_render_done,  workers=critic_workers,  queue_size=queue_size)
        self.bus.register_actor("patcher", self._on_critic_done,  workers=patcher_workers, queue_size=queue_size)
        self.bus.register_actor("terminal",self._on_terminal,     workers=1,               queue_size=queue_size)

        # Subscribe to events
        self.bus.subscribe("task.created",   "coder")
        self.bus.subscribe("coder.done",     "checker")
        self.bus.subscribe("checker.ok",     "renderer")
        self.bus.subscribe("checker.failed", "coder")
        self.bus.subscribe("render.done",    "critic")
        self.bus.subscribe("critic.done",    "patcher")
        self.bus.subscribe("patcher.done",   "checker")
        self.bus.subscribe("task.done",      "terminal")
        self.bus.subscribe("task.failed",    "terminal")

    # Lifecycle

    async def start(self) -> None:
        await self.bus.start()

    async def stop(self) -> None:
        await self.bus.stop()
        for task in list(self._tasks.values()):
            if task.deadline_task and not task.deadline_task.done():
                task.deadline_task.cancel()
        for stem, future in list(self._futures.items()):
            if not future.done():
                future.cancel()
            self._futures.pop(stem, None)

    # API

    async def submit(self, task: PipelineTask) -> asyncio.Future[PipelineTask]:
        """Register a PipelineTask and kick off the pipeline.

        Returns a `Future` that resolves to the same task envelope once
        it reaches a terminal state (`task.done` or `task.failed`). The
        envelope is mutated in-place, so callers that already retain a
        reference can observe the same downstream state directly.
        """
        future: asyncio.Future[PipelineTask] = asyncio.get_running_loop().create_future()
        self._futures[task.stem] = future
        self._tasks[task.stem] = task
        task.started_at = time.time()
        task.deadline_task = asyncio.create_task(
            self._deadline_watcher(task.stem),
            name=f"deadline.{task.stem}",
        )
        logger.info(
            f"{_stg(1,'Coder')} Submitted Task {task.stem} | Seed: {task.seed} | URL: {task.image_url[:80]}"
        )
        try:
            t_fetch = time.time()
            task.image_bytes, task.image_mime = await download_image(
                task.image_url, self.http_client,
            )
            logger.info(
                f"{_stg(1,'Coder')} Downloaded Task {task.stem} | MIME: {task.image_mime} | Bytes: {len(task.image_bytes)} | Elapsed: {time.time() - t_fetch:.2f}s"
            )
        except Exception as exc:
            await self._fail(task.stem, f"fetch: {type(exc).__name__}: {exc}", stage="fetch")
            return future
        await self.bus.publish(TaskCreated(task_id=task.stem, image_url=task.image_url, seed=task.seed))
        return future

    # Handlers

    async def _on_coder_input(self, event: Event) -> None:
        """Handles both task.created (fresh) and checker.failed (repair)."""
        task = self._tasks.get(event.task_id)
        if task is None or task.terminal:
            return
        try:
            _mode = "repair" if isinstance(event, CheckerFailed) else "fresh code"
            logger.info(f"{_stg(1,'Coder')} Event: {event.type} | Task {task.stem} | Iter: {task.iteration} | Mode: {_mode}")
            if isinstance(event, TaskCreated):
                js_code = await self.coder.code(
                    task_id=task.stem,
                    image_bytes=task.image_bytes,
                    image_url=task.image_url,
                    image_mime=task.image_mime,
                )
            elif isinstance(event, CheckerFailed):
                js_code = await self.coder.code_repair(
                    task_id=task.stem, js_errors=list(event.js_errors),
                )
            else:
                logger.warning(f"coder handler got unexpected event type={event.type}")
                return
            task.js_code = js_code
            await self.bus.publish(CoderDone(task_id=task.stem, js_code=js_code))
        except Exception as exc:
            await self._fail(task.stem, f"coder: {type(exc).__name__}: {exc}", stage="coder")

    async def _on_coder_done(self, event: Event) -> None:
        """Consumes coder.done and patcher.done. Runs JS Checker on task.js_code."""
        task = self._tasks.get(event.task_id)
        if task is None or task.terminal:
            return
        logger.info(f"{_stg(2,'Checker')} Event: {event.type} | Task {task.stem} | Iter: {task.iteration}")
        if not isinstance(task.js_code, str) or not task.js_code:
            await self._fail(task.stem, "coder returned empty js_code", stage="coder")
            return
        # Reset checker output before re-running.
        task.js_valid = None
        task.js_errors = []
        task.failed = False
        task.failure_reason = None
        try:
            await self.js_checker.process(task)
        except Exception as exc:
            await self._fail(task.stem, f"checker: {type(exc).__name__}: {exc}", stage="checker")
            return
        if task.js_valid:
            await self.bus.publish(CheckerOk(
                task_id=task.stem, js_code=task.js_code,
                metrics=dict(task.js_metrics or {}),
            ))
        else:
            await self.bus.publish(CheckerFailed(
                task_id=task.stem, js_errors=list(task.js_errors or []),
            ))

    async def _on_checker_ok(self, event: Event) -> None:
        assert isinstance(event, CheckerOk)
        task = self._tasks.get(event.task_id)
        if task is None or task.terminal:
            return
        logger.info(f"{_stg(3,'Renderer')} Event: {event.type} | Task {task.stem} | Iter: {task.iteration}")

        task.render_errors = []
        task.failed = False
        task.failure_reason = None
        try:
            await self.renderer.process(task)
        except Exception as exc:
            await self._fail(task.stem, f"renderer: {type(exc).__name__}: {exc}", stage="renderer")
            return
        if task.failed or task.rendered_png is None:
            reason = task.failure_reason or (task.render_errors[0] if task.render_errors else "no png")
            await self._fail(task.stem, f"renderer: {reason}", stage="renderer")
            return
        await self.bus.publish(RenderDone(
            task_id=task.stem, rendered_png=task.rendered_png,
        ))

    async def _on_render_done(self, event: Event) -> None:
        assert isinstance(event, RenderDone)
        task = self._tasks.get(event.task_id)
        if task is None or task.terminal:
            return
        logger.info(f"{_stg(4,'Critic')} Event: {event.type} | Task {task.stem} | Iter: {task.iteration}")
        if task.image_bytes is None or task.js_code is None or task.rendered_png is None:
            await self._fail(task.stem, "critic: missing inputs", stage="critic")
            return
        artifact_context = {
            "kind": "coder_v1",
            "js_code": task.js_code,
        }
        try:
            report = await self.critic.critique(
                task_id=task.stem,
                image_bytes=task.image_bytes,
                image_mime=task.image_mime,
                render_png=task.rendered_png,
                artifact_context=artifact_context,
            )
        except Exception as exc:
            await self._fail(task.stem, f"critic: {type(exc).__name__}: {exc}", stage="critic")
            return
        await self.bus.publish(CriticDone(task_id=task.stem, report=report))

    async def _on_critic_done(self, event: Event) -> None:
        assert isinstance(event, CriticDone)
        task = self._tasks.get(event.task_id)
        if task is None or task.terminal:
            return
        report = event.report

        task.score_history.append(report.overall_score)
        if report.overall_score > task.best_score:
            task.best_score = report.overall_score
            task.best_iter = task.iteration
            task.best_js_code = task.js_code
            task.best_rendered_png = task.rendered_png
            logger.info(
                f"{_stg(4,'Critic')} Event: {event.type} | Task {task.stem} | Iter: {task.iteration} | Score: {report.overall_score:.2f} | Best Updated"
            )
        # Happy path: critic is satisfied → complete.
        if report.stop or report.overall_score >= self.score_threshold or not report.issues:
            reason = (
                "stop_flag" if report.stop else
                "threshold_met" if report.overall_score >= self.score_threshold else
                "no_issues"
            )
            logger.info(
                f"{_stg(4,'Critic')} Event: {event.type} | Task {task.stem} | Iter: {task.iteration} | Score: {report.overall_score:.2f} | Issues: {len(report.issues)} | Accepted: {reason}"
            )
            await self._complete(task.stem)
            return
        # Out of iterations: serve the best snapshot.
        if task.iteration >= self.max_iter:
            logger.info(
                f"{_stg(4,'Critic')} Event: {event.type} | Task {task.stem} | Iter: {task.iteration} | Score: {report.overall_score:.2f} | Best: {task.best_score:.2f} | History: {task.score_history} | Max Iter Reached"
            )
            await self._complete(task.stem)
            return
        # F1 adaptive abort — give up early on hopeless tasks.
        if task.iteration >= 1 and task.best_score < 0.20:
            logger.info(
                f"{_stg(4,'Critic')} Event: {event.type} | Task {task.stem} | Iter: {task.iteration} | Best: {task.best_score:.2f} | Adaptive Abort"
            )
            await self._complete(task.stem)
            return
        # Patch round.
        issue_kinds: dict[str, int] = {}
        for issue in report.issues:
            k = getattr(issue.kind, "value", str(issue.kind))
            issue_kinds[k] = issue_kinds.get(k, 0) + 1
        logger.info(
            f"{_stg(5,'Patcher')} Event: {event.type} | Task {task.stem} | Iter: {task.iteration} | Score: {report.overall_score:.2f} | Issues: {len(report.issues)} | By Kind: {issue_kinds}"
        )
        try:
            js_code = await self.coder.code_critic_repair(
                task_id=task.stem,
                issues=report.issues,
                overall_score=report.overall_score,
                matching_aspects=list(getattr(report, "matching_aspects", []) or []),
                image_bytes=task.image_bytes if self.coder_multimodal else None,
                image_mime=task.image_mime,
                render_png=task.rendered_png if self.coder_multimodal else None,
            )
            task.js_code = js_code
            task.iteration += 1
            applied_ops = self._issue_labels(report.issues)
            await self.bus.publish(PatcherDone(
                task_id=task.stem, js_code=js_code,
                applied_ops=applied_ops, iteration=task.iteration,
            ))
        except Exception as exc:
            await self._fail(task.stem, f"patcher: {type(exc).__name__}: {exc}", stage="patcher")

    async def _on_terminal(self, event: Event) -> None:
        task = self._tasks.get(event.task_id)
        if task is None:
            return
        task.terminal = True
        if task.deadline_task and not task.deadline_task.done():
            task.deadline_task.cancel()
        self.session_store.evict(event.task_id)
        self._tasks.pop(event.task_id, None)
        future = self._futures.pop(event.task_id, None)
        if future is not None and not future.done():
            future.set_result(task)
        total_elapsed = time.time() - task.started_at
        if isinstance(event, TaskDone):
            logger.info(
                f"{_stg(6,'Done')} Event: {event.type} | Task {task.stem} | Iter: {task.iteration} | Elapsed: {total_elapsed:.1f}s | Patches: {task.iteration}"
            )
        else:
            reason = getattr(event, "error", "?")
            stage = getattr(event, "stage", None)
            logger.warning(
                f"{_stg(6,'Failed')} Event: {event.type} | Task {task.stem} | Reason: {reason} | Stage: {stage} | Elapsed: {total_elapsed:.1f}s"
            )

    # Helpers

    @staticmethod
    def _issue_labels(issues: list) -> list[str]:
        labels: list[str] = []
        for issue in issues:
            if isinstance(issue, dict):
                kind = issue.get("kind", "issue")
                desc = issue.get("description", "")
            else:
                kind = getattr(issue, "kind", "issue")
                if hasattr(kind, "value"):
                    kind = kind.value
                desc = getattr(issue, "description", "")
            labels.append(f"{kind}:{str(desc)[:80]}")
        return labels

    async def _complete(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is None or task.terminal:
            return
        if task.best_js_code is not None:
            task.js_code = task.best_js_code
            task.rendered_png = task.best_rendered_png
            logger.info(
                f"{_stg(6,'Done')} Event: task.done | Task {task.stem} | Source: best_iter | Best Iter: {task.best_iter} | Best Score: {task.best_score:.2f} | History: {task.score_history}"
            )
        else:
            logger.info(
                f"{_stg(6,'Done')} Event: task.done | Task {task.stem} | Source: last_state"
            )
        artifact = {
            "js": task.js_code,
            "rendered_png": task.rendered_png,
        }
        task.failed = False
        task.failure_reason = None
        task.failure_stage = None
        await self.bus.publish(TaskDone(task_id=task_id, artifact=artifact))

    async def _fail(self, task_id: str, reason: str, *, stage: str | None = None) -> None:
        task = self._tasks.get(task_id)
        if task is None or task.terminal:
            return
        task.failed = True
        task.failure_reason = reason
        task.failure_stage = stage
        await self.bus.publish(TaskFailed(task_id=task_id, error=reason, stage=stage))

    async def _deadline_watcher(self, task_id: str) -> None:
        try:
            await asyncio.sleep(self.task_deadline_s)
            task = self._tasks.get(task_id)
            if task is not None and not task.terminal:
                await self._fail(task_id, "budget exceeded", stage="deadline")
        except asyncio.CancelledError:
            return
