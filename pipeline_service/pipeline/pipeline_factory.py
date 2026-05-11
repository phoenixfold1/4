from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from config.settings import SettingsConf
from llm.session_store import SessionStore
from modules.critic.agent import CriticAgent
from modules.js_checker.module import JSCheckerModule
from modules.renderer.module import RendererModule
from modules.scene_coder.agent import SceneCoderAgent
from pipeline.bus import EventBus
from pipeline.orchestrator import Orchestrator
from pipeline.task import PipelineTask


@dataclass
class Pipeline:
    """Bundle of the full graph. Hold onto this across app lifecycle."""

    orchestrator: Orchestrator
    bus: EventBus
    session_store: SessionStore
    coder: SceneCoderAgent

    async def start(self) -> None:
        await self.orchestrator.start()

    async def stop(self) -> None:
        await self.orchestrator.stop()

    async def submit(self, task: PipelineTask) -> asyncio.Future[PipelineTask]:
        return await self.orchestrator.submit(task)


def build_pipeline(
    *,
    settings: SettingsConf,
    clients: dict[str, Any],
    js_checker: JSCheckerModule,
    renderer: RendererModule,
    http_client: httpx.AsyncClient,
    session_store: SessionStore | None = None,
) -> Pipeline:
    """Wire agents + orchestrator from settings."""
    session_store = session_store or SessionStore()
    actors = settings.actors
    policy = settings.event_bus
    llm = settings.llm_clients

    def _backend(actor_client: str) -> str:
        cfg = llm.get(actor_client)
        return cfg.backend if cfg is not None else "openrouter"

    coder = SceneCoderAgent(
        client=clients[actors.coder.client],
        model=actors.coder.model,
        session_store=session_store,
        temperature=actors.coder.temperature,
        seed=actors.coder.seed,
        max_tokens=actors.coder.max_tokens,
        backend=_backend(actors.coder.client),
    )

    critic = CriticAgent(
        client=clients[actors.critic.client],
        model=actors.critic.model,
        max_tokens=actors.critic.max_tokens,
        seed=actors.critic.seed,
        reasoning_effort=actors.critic.reasoning_effort,
        ensemble_size=actors.critic.ensemble_size,
        backend=_backend(actors.critic.client),
    )

    bus = EventBus()
    orchestrator = Orchestrator(
        bus=bus,
        session_store=session_store,
        coder=coder,
        critic=critic,
        js_checker=js_checker,
        renderer=renderer,
        http_client=http_client,
        coder_multimodal=actors.coder.multimodal,
        task_deadline_s=policy.task_deadline_s,
        max_iter=policy.max_iter,
        score_threshold=policy.score_threshold,
        coder_workers=actors.coder.workers,
        checker_workers=actors.checker.workers,
        renderer_workers=actors.renderer.workers,
        critic_workers=actors.critic.workers,
        patcher_workers=actors.patcher.workers,
        queue_size=max(
            actors.coder.queue_size,
            actors.checker.queue_size,
            actors.renderer.queue_size, actors.critic.queue_size,
            actors.patcher.queue_size,
        ),
    )

    return Pipeline(
        orchestrator=orchestrator,
        bus=bus,
        session_store=session_store,
        coder=coder,
    )
