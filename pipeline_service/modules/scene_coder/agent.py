from __future__ import annotations

import base64
import json
import time
from typing import Any

from llm.session import SessionAgent
from llm.session_store import SessionStore
from logger_config import logger
from modules.critic.schema import Issue
from modules.scene_coder.prompts import (
    CODER_SYSTEM_PROMPT,
    CODER_USER_TEMPLATE_CHECKER_REPAIR,
    CODER_USER_TEMPLATE_CRITIC_REPAIR,
    CODER_USER_TEMPLATE_FRESH,
)

_ACTOR = "coder"


class SceneCoderAgent:
    """Per-pipeline JS code generator."""

    actor = _ACTOR

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        session_store: SessionStore,
        temperature: float = 0.0,
        seed: int | None = 42,
        max_tokens: int = 8192,
        max_tool_iters: int = 4,
        max_output_retries: int = 2,
        backend: str = "openrouter",
    ) -> None:
        self.client = client
        self.model = model
        self.session_store = session_store
        self.temperature = temperature
        self.seed = seed
        self.max_tokens = max_tokens
        self.max_tool_iters = max_tool_iters
        self.max_output_retries = max_output_retries
        self.backend = backend

    def _build_session(self, task_id: str, actor: str) -> SessionAgent:
        return SessionAgent(
            task_id=task_id,
            actor=actor,
            system_prompt=CODER_SYSTEM_PROMPT,
            model=self.model,
            tools=None,
            response_model=None,
            client=self.client,
            temperature=self.temperature,
            seed=self.seed,
            max_tokens=self.max_tokens,
            max_tool_iters=self.max_tool_iters,
            backend=self.backend,
        )

    async def code(
        self,
        task_id: str,
        *,
        image_bytes: bytes | None = None,
        image_url: str | None = None,
        image_mime: str = "image/jpeg",
    ) -> str:
        if not image_bytes and not image_url:
            raise ValueError("code() requires image_bytes or image_url")
        session = self.session_store.get_or_create(
            task_id, self.actor, self._build_session,
        )
        if image_bytes:
            ref_b64 = base64.b64encode(image_bytes).decode()
            url_str = f"data:{image_mime};base64,{ref_b64}"
        else:
            url_str = image_url  # type: ignore[assignment]
        user_content: list[dict[str, Any]] = [
            {"type": "image_url", "image_url": {"url": url_str}},
            {"type": "text", "text": CODER_USER_TEMPLATE_FRESH},
        ]
        logger.info(
            f"[1/6 Coder] Started Task {task_id} | Model: {self.model} | Image KB: {(len(image_bytes) / 1024 if image_bytes else 0):.1f}"
        )
        t0 = time.time()
        js_code = await self._run_until_valid_js(session, user_content)
        dt = time.time() - t0
        logger.info(
            f"[1/6 Coder] Finished Task {task_id} | Elapsed: {dt:.1f}s | Bytes: {len(js_code.encode('utf-8'))} | Lines: {len(js_code.splitlines())}"
        )
        return js_code

    async def code_repair(
        self,
        task_id: str,
        *,
        js_errors: list[str],
    ) -> str:
        session = self.session_store.get(task_id, self.actor)
        if session is None:
            raise RuntimeError(
                f"Code repair called for Task ID: {task_id} but no Coder session exists; "
                "code() must run first."
            )
        errors_block = "\n".join(f"- {e}" for e in js_errors) or "- (no specific errors returned)"
        user_msg = CODER_USER_TEMPLATE_CHECKER_REPAIR.format(
            errors_block=errors_block,
        )
        logger.info(
            f"[Coder Repair] Started Task {task_id} | Repair: checker | Errors: {len(js_errors)}"
        )
        t0 = time.time()
        js_code = await self._run_until_valid_js(session, user_msg)
        dt = time.time() - t0
        logger.info(
            f"[Coder Repair] Finished Task {task_id} | Elapsed: {dt:.1f}s | Bytes: {len(js_code.encode('utf-8'))}"
        )
        return js_code

    async def code_critic_repair(
        self,
        task_id: str,
        *,
        issues: list[Issue] | list[dict[str, Any]],
        overall_score: float,
        matching_aspects: list[str] | None = None,
        image_bytes: bytes | None = None,
        image_mime: str = "image/png",
        render_png: bytes | None = None,
    ) -> str:
        """Run the Coder in repair mode."""
        session = self.session_store.get(task_id, self.actor)
        if session is None:
            raise RuntimeError(
                f"code_critic_repair called for Task ID: {task_id} but no Coder session exists; "
                "code() must run first."
            )
        normalized_issues = []
        for issue in issues:
            if hasattr(issue, "model_dump"):
                normalized_issues.append(issue.model_dump(mode="json"))
            else:
                normalized_issues.append(issue)
        matching_block = (
            "\n".join(f"- {m}" for m in matching_aspects)
            if matching_aspects else "- (none flagged by critic — proceed carefully)"
        )
        user_text = CODER_USER_TEMPLATE_CRITIC_REPAIR.format(
            overall_score=f"{overall_score:.2f}",
            issues_json=json.dumps(normalized_issues, indent=2, ensure_ascii=False),
            matching_block=matching_block,
        )
        multimodal = image_bytes is not None and render_png is not None
        user_content: str | list[dict[str, Any]]
        if multimodal:
            ref_b64 = base64.b64encode(image_bytes).decode()
            render_b64 = base64.b64encode(render_png).decode()
            user_content = [
                {"type": "text",
                 "text": "Below: (1) the REFERENCE image we're trying to match, "
                         "and (2) the RENDER of your previous JS module. "
                         "Compare them yourself and decide what to fix. "
                         "Critic feedback follows but you should rely on the "
                         "visual comparison primarily.\n"},
                {"type": "image_url",
                 "image_url": {"url": f"data:{image_mime};base64,{ref_b64}"}},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{render_b64}"}},
                {"type": "text", "text": user_text},
            ]
        else:
            user_content = user_text

        logger.info(
            f"[5/6 Patcher] Started Task {task_id} | Repair: critic | Issues: {len(normalized_issues)} | Score: {overall_score:.2f} | Multimodal: {multimodal}"
        )
        t0 = time.time()
        js_code = await self._run_until_valid_js(session, user_content)
        dt = time.time() - t0
        logger.info(
            f"[5/6 Patcher] Finished Task {task_id} | Elapsed: {dt:.1f}s | Bytes: {len(js_code.encode('utf-8'))}"
        )
        return js_code

    async def _run_until_valid_js(
        self,
        session: SessionAgent,
        user_msg: str | list[dict[str, Any]],
    ) -> str:
        raw = await session.run(user_msg)
        js = self._normalize_js_output(str(raw))
        for attempt in range(self.max_output_retries + 1):
            if self._looks_like_js_module(js):
                return js
            if attempt >= self.max_output_retries:
                break
            raw = await session.run(
                "Your previous response was not a valid raw JavaScript module. "
                "Return ONLY the full JS source with the exact signature "
                "`export default function generate(THREE)` and no markdown fences."
            )
            js = self._normalize_js_output(str(raw))
        raise ValueError(
            "Coder did not return a valid JS module with "
            "`export default function generate(THREE)`."
        )

    @staticmethod
    def _normalize_js_output(raw: str) -> str:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return text

    @staticmethod
    def _looks_like_js_module(text: str) -> bool:
        return (
            "export default function generate(THREE)" in text
            and "return" in text
        )
