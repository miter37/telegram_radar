"""LLM engine registry: multiple engines with priority/fallback support.

Engine types:
- openai_compatible: standard base_url + api_key
- openai_codex: OpenAI with OAuth/Codex auth (use api_key as bearer; same wire format)
- zai: Zhipu z.ai (OpenAI-compatible at /api/paas/v4)
- minimax: MiniMax API (api.minimax.chat, OpenAI-compatible)
- custom: user-defined

Each engine has priority (1=primary). On LLM call, try engines in priority
order until one succeeds.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from ..config import DATA_DIR

logger = logging.getLogger(__name__)


# Built-in provider presets
PROVIDER_PRESETS: dict[str, dict] = {
    "openai_compatible": {
        "label": "OpenAI 호환 (LM Studio, vLLM, Ollama 등)",
        "base_url": "http://127.0.0.1:18085/v1",
        "api_key_hint": "not-needed",
    },
    "openai": {
        "label": "OpenAI (api.openai.com)",
        "base_url": "https://api.openai.com/v1",
        "api_key_hint": "sk-...",
    },
    "openai_codex": {
        "label": "OpenAI Codex (OAuth/Codex CLI 인증)",
        "base_url": "https://api.openai.com/v1",
        "api_key_hint": "codex CLI 로그인 (auto-refresh)",
    },
    "zai": {
        "label": "Z.AI (z.ai coding plan)",
        "base_url": "https://api.z.ai/api/paas/v4",
        "api_key_hint": "Z.AI API key",
    },
    "minimax": {
        "label": "MiniMax coding plan",
        "base_url": "https://api.minimaxi.com/v1",
        "api_key_hint": "MiniMax API key",
    },
    "anthropic": {
        "label": "Anthropic (Claude) — OpenAI 호환 게이트웨이 경유",
        "base_url": "https://api.anthropic.com/v1",
        "api_key_hint": "sk-ant-...",
    },
    "google": {
        "label": "Google (Gemini) — OpenAI 호환 게이트웨이 경유",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_key_hint": "AIza...",
    },
    "custom": {
        "label": "Custom (직접 입력)",
        "base_url": "",
        "api_key_hint": "",
    },
}


@dataclass
class Engine:
    id: str
    name: str
    provider: str
    base_url: str
    api_key: str = ""
    model: str = ""
    priority: int = 1
    enabled: bool = True
    extra_headers: str = ""   # JSON string, e.g. {"X-Custom": "value"}
    timeout: float = 60.0
    created_at: str = ""
    last_ok_at: str = ""
    last_error: str = ""
    use_codex_oauth: bool = False   # if True, ignore api_key and read ~/.codex/auth.json

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Engine":
        return cls(
            id=str(d.get("id") or uuid.uuid4()),
            name=str(d.get("name") or "이름 없음"),
            provider=str(d.get("provider") or "openai_compatible"),
            base_url=str(d.get("base_url") or ""),
            api_key=str(d.get("api_key") or ""),
            model=str(d.get("model") or ""),
            priority=int(d.get("priority") or 1),
            enabled=bool(d.get("enabled", True)),
            extra_headers=str(d.get("extra_headers") or ""),
            timeout=float(d.get("timeout") or 60.0),
            created_at=str(d.get("created_at") or ""),
            last_ok_at=str(d.get("last_ok_at") or ""),
            last_error=str(d.get("last_error") or ""),
            use_codex_oauth=bool(d.get("use_codex_oauth", False)),
        )


def _engines_path() -> Path:
    return DATA_DIR / "settings" / "llm_engines.json"


class EngineRegistry:
    """Persistent list of LLM engines with priority ordering."""

    def __init__(self):
        self._engines: list[Engine] = []
        self._load()

    def _load(self) -> None:
        p = _engines_path()
        if not p.exists():
            # seed with default engine from env
            from ..config import load_llm_config
            cfg = load_llm_config()
            self._engines = [
                Engine(
                    id=str(uuid.uuid4()),
                    name="기본 (env)",
                    provider="openai_compatible",
                    base_url=cfg.base_url,
                    api_key=cfg.api_key,
                    model=cfg.model,
                    priority=1,
                    enabled=True,
                )
            ]
            return
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            self._engines = [Engine.from_dict(x) for x in d.get("engines", [])]
        except Exception:
            self._engines = []

    def save(self) -> None:
        p = _engines_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"engines": [e.to_dict() for e in self._engines]},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def list(self) -> list[Engine]:
        return list(self._engines)

    def list_enabled(self) -> list[Engine]:
        return sorted([e for e in self._engines if e.enabled], key=lambda x: x.priority)

    def get(self, engine_id: str) -> Optional[Engine]:
        for e in self._engines:
            if e.id == engine_id:
                return e
        return None

    def add(self, engine: Engine) -> Engine:
        if not engine.id:
            engine.id = str(uuid.uuid4())
        if not engine.created_at:
            engine.created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self._engines.append(engine)
        self.save()
        return engine

    def update(self, engine: Engine) -> bool:
        for i, e in enumerate(self._engines):
            if e.id == engine.id:
                self._engines[i] = engine
                self.save()
                return True
        return False

    def remove(self, engine_id: str) -> bool:
        before = len(self._engines)
        self._engines = [e for e in self._engines if e.id != engine_id]
        if len(self._engines) < before:
            self.save()
            return True
        return False

    def mark_ok(self, engine_id: str) -> None:
        for e in self._engines:
            if e.id == engine_id:
                e.last_ok_at = datetime.now().astimezone().isoformat(timespec="seconds")
                e.last_error = ""
                self.save()
                return

    def mark_error(self, engine_id: str, error: str) -> None:
        for e in self._engines:
            if e.id == engine_id:
                e.last_error = error
                self.save()
                return

    def next_priority(self) -> int:
        if not self._engines:
            return 1
        return max(e.priority for e in self._engines) + 1


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------- Engine health probe ----------

async def probe_engine(engine: Engine, *, timeout: float = 8.0) -> tuple[bool, str]:
    """Try GET /models on the engine. Returns (ok, message)."""
    if not engine.base_url:
        return False, "base_url이 비어있음"
    api_key = engine.api_key
    if engine.use_codex_oauth or engine.provider == "openai_codex":
        try:
            from .codex_auth import get_codex_token
            api_key = get_codex_token() or ""
            if not api_key:
                return False, "Codex auth.json 토큰 없음"
        except Exception as e:
            return False, f"codex auth: {e}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(
                f"{engine.base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key or 'not-needed'}"},
            )
            r.raise_for_status()
            data = r.json()
            models = data.get("data") or data.get("models") or []
            names = [m.get("id") or m.get("name") for m in models[:5]]
            return True, f"OK · {len(models)}개 모델" + (
                f" ({', '.join(names[:3])})" if names else ""
            )
    except Exception as e:
        return False, f"{e.__class__.__name__}: {e}"


# ---------- Multi-engine router ----------

@dataclass
class CompletionResult:
    ok: bool
    content: str = ""
    payload: Optional[dict] = None
    engine_id: str = ""
    engine_name: str = ""
    error: str = ""


async def chat_completion(
    engines: list[Engine],
    *,
    messages: list[dict],
    temperature: float = 0.3,
    top_p: float = 0.9,
    max_tokens: int = 1500,
    extra: Optional[dict] = None,
) -> CompletionResult:
    """Try each enabled engine in priority order. Return first success."""
    last_error = ""
    for eng in engines:
        if not eng.enabled:
            continue
        if not eng.base_url:
            last_error = f"[{eng.name}] base_url 비어있음"
            continue

        # Resolve api_key (Codex OAuth path)
        api_key = eng.api_key
        if eng.use_codex_oauth or eng.provider == "openai_codex":
            try:
                from .codex_auth import get_codex_token
                api_key = get_codex_token() or ""
                if not api_key:
                    last_error = f"[{eng.name}] Codex auth.json 토큰 없음"
                    continue
            except Exception as e:
                last_error = f"[{eng.name}] codex auth: {e}"
                continue

        try:
            body = {
                "model": eng.model,
                "messages": messages,
                "temperature": temperature,
                "top_p": top_p,
                "max_tokens": max_tokens,
            }
            if extra:
                body.update(extra)
            headers = {"Authorization": f"Bearer {api_key or 'not-needed'}"}
            if eng.extra_headers.strip():
                try:
                    extra_h = json.loads(eng.extra_headers)
                    if isinstance(extra_h, dict):
                        headers.update(extra_h)
                except Exception:
                    pass
            async with httpx.AsyncClient(timeout=eng.timeout) as client:
                r = await client.post(
                    f"{eng.base_url.rstrip('/')}/chat/completions",
                    json=body, headers=headers,
                )
                r.raise_for_status()
                data = r.json()
                content = data["choices"][0]["message"]["content"]
                return CompletionResult(
                    ok=True, content=content, engine_id=eng.id,
                    engine_name=eng.name,
                )
        except Exception as e:
            last_error = f"[{eng.name}] {e.__class__.__name__}: {e}"
            logger.warning("engine %s failed: %s", eng.name, last_error)
            continue
    return CompletionResult(ok=False, error=last_error or "all engines failed")
