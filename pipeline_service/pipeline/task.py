from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PipelineTask:
    """Single envelope threaded through every pipeline stage."""

    # Input
    stem: str
    image_url: str
    seed: int = 42

    # Fetched image
    image_bytes: bytes | None = None
    image_mime: str = "image/jpeg"

    # Coder → JS
    js_code: str | None = None

    # JS Checker (mutated by JSCheckerModule.process)
    js_valid: bool | None = None
    js_errors: list[str] = field(default_factory=list)
    js_metrics: dict | None = None
    js_stages_run: list[str] = field(default_factory=list)
    js_module_load_ms: float | None = None
    js_execution_ms: float | None = None
    js_total_ms: float | None = None

    # Renderer (mutated by RendererModule.process)
    rendered_png: bytes | None = None              # 2x2 grid
    render_ms: float | None = None
    render_errors: list[str] = field(default_factory=list)

    # Refinement state (orchestrator)
    iteration: int = 0
    score_history: list[float] = field(default_factory=list)
    best_score: float = -1.0
    best_iter: int = -1
    best_js_code: str | None = None
    best_rendered_png: bytes | None = None

    # Lifecycle (orchestrator-private — not part of the public artifact)
    started_at: float = 0.0
    terminal: bool = False
    deadline_task: asyncio.Task | None = field(default=None, repr=False, compare=False)

    # Status
    failed: bool = False
    failure_reason: str | None = None
    failure_stage: str | None = None
    attempt: int = 0

    meta: dict[str, Any] = field(default_factory=dict)

    # Monitoring
    oom_retries: int = 0

