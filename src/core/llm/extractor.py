"""OpenAI-compatible LLM extractor.

Calls /v1/chat/completions with overridden temperature. Strips
<think>...</think> reasoning blocks (Gemma 4 mode) before JSON parsing.

Phase: multi-engine support. LLMExtractor now uses the engine registry:
the env vars (TG_LLM_*) are the default fallback. If engines are registered
in data/settings/llm_engines.json, they are tried in priority order.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from .engines import (
    Engine,
    EngineRegistry,
    chat_completion as _multi_chat_completion,
)
from .prompts import CURRENT_VERSION, render_prompt, split_system_and_input
from .validator import coerce, validate

logger = logging.getLogger(__name__)

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class ExtractionResult:
    ok: bool
    payload: Optional[dict] = None
    raw_text: str = ""
    error: Optional[str] = None
    prompt_version: str = CURRENT_VERSION
    engine_used: str = ""   # name of engine that succeeded (or empty)


class LLMExtractor:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._resolved_model: Optional[str] = None
        self._client: Optional[httpx.AsyncClient] = None
        # multi-engine support
        self._registry: Optional[EngineRegistry] = None

    def set_registry(self, registry: EngineRegistry) -> None:
        """Provide an engine registry for multi-engine / fallback support."""
        self._registry = registry

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        return self._client

    async def resolve_model(self) -> str:
        """Query /v1/models and pick the first available. Cache result."""
        if self._resolved_model:
            return self._resolved_model
        try:
            client = await self._ensure_client()
            r = await client.get(f"{self.base_url}/models")
            r.raise_for_status()
            data = r.json()
            models = data.get("data") or data.get("models") or []
            if models:
                first = models[0]
                self._resolved_model = first.get("id") or first.get("name") or self.model
                return self._resolved_model
        except Exception as e:
            logger.warning("model resolve failed (%s), using env default: %s", e, self.model)
        self._resolved_model = self.model
        return self._resolved_model

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    @staticmethod
    def _strip_think(text: str) -> str:
        cleaned = THINK_RE.sub("", text)
        return cleaned.strip()

    @staticmethod
    def _extract_json(text: str) -> Optional[dict]:
        try:
            return json.loads(text)
        except Exception:
            pass
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
        m = JSON_RE.search(text)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return None

    async def _call_via_registry(
        self,
        messages: list[dict],
        *,
        temperature: float,
        max_tokens: int,
    ) -> tuple[Optional[str], Optional[str]]:
        """Try engines in priority order. Returns (content, error)."""
        if self._registry is None:
            return None, "no registry"
        engines = self._registry.list_enabled()
        if not engines:
            return None, "no enabled engines"
        res = await _multi_chat_completion(
            engines,
            messages=messages,
            temperature=temperature,
            top_p=0.9,
            max_tokens=max_tokens,
        )
        if res.ok:
            self._registry.mark_ok(res.engine_id)
            return res.content, None
        self._registry.mark_error(res.engine_id, res.error) if res.engine_id else None
        return None, res.error

    async def extract(
        self,
        *,
        datetime: str,
        channel_name: str,
        message_text: str,
        message_url: Optional[str],
        user_interests: list[str],
        prompt_version: str = CURRENT_VERSION,
        temperature: float = 0.3,
        max_retries: int = 2,
    ) -> ExtractionResult:
        rendered = render_prompt(
            version=prompt_version,
            datetime=datetime,
            channel_name=channel_name,
            message_text=message_text,
            message_url=message_url,
            user_interests=user_interests,
        )
        system, user_payload = split_system_and_input(rendered)

        # multi-engine path
        if self._registry is not None and self._registry.list_enabled():
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user_payload},
            ]
            for attempt in range(max_retries + 1):
                content, err = await self._call_via_registry(
                    messages,
                    temperature=temperature if attempt == 0 else 0.2,
                    max_tokens=800,
                )
                if content is None:
                    return ExtractionResult(
                        ok=False, error=err or "all engines failed",
                        prompt_version=prompt_version,
                    )
                stripped = self._strip_think(content)
                payload = self._extract_json(stripped)
                if payload is None:
                    continue
                payload = coerce(payload)
                ok, v_err = validate(payload)
                if not ok:
                    continue
                return ExtractionResult(
                    ok=True, payload=payload, raw_text=content,
                    prompt_version=prompt_version,
                )
            return ExtractionResult(
                ok=False, error="JSON parse/validate failed across retries",
                prompt_version=prompt_version,
            )

        # legacy single-engine path (env config)
        model_name = await self.resolve_model()
        client = await self._ensure_client()
        last_error: Optional[str] = None
        for attempt in range(max_retries + 1):
            try:
                body = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_payload},
                    ],
                    "temperature": temperature if attempt == 0 else 0.2,
                    "top_p": 0.9,
                    "max_tokens": 800,
                    "stream": False,
                    # Disable thinking/reasoning for Qwen3 / DeepSeek-R1 style models
                    # so the response token budget goes to the JSON payload,
                    # not to internal reasoning chains. llama.cpp's OpenAI
                    # server reads this from chat_template_kwargs.
                    "chat_template_kwargs": {"enable_thinking": False},
                    "reasoning_effort": "low",
                }
                r = await client.post(f"{self.base_url}/chat/completions", json=body)
                r.raise_for_status()
                data = r.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                raw_text = content or ""
                stripped = self._strip_think(raw_text)
                payload = self._extract_json(stripped)
                if payload is None:
                    last_error = "no JSON in response"
                    logger.warning("attempt %d: %s; raw=%r", attempt, last_error, raw_text[:200])
                    continue
                payload = coerce(payload)
                ok, err = validate(payload)
                if not ok:
                    last_error = err or "validation failed"
                    logger.warning("attempt %d: %s", attempt, last_error)
                    continue
                return ExtractionResult(ok=True, payload=payload, raw_text=raw_text, prompt_version=prompt_version)
            except httpx.HTTPError as e:
                last_error = f"http: {e.__class__.__name__}: {e}"
                logger.warning("attempt %d: %s", attempt, last_error)
            except Exception as e:
                last_error = f"{e.__class__.__name__}: {e}"
                logger.exception("attempt %d: %s", attempt, last_error)

        return ExtractionResult(
            ok=False,
            payload=None,
            raw_text="",
            error=last_error or "unknown",
            prompt_version=prompt_version,
        )
