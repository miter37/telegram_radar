"""Configuration loading from environment variables.

Reads TG_* env vars (Telegram auth) and TG_LLM_* env vars (LLM endpoint).
If a .env file exists in the project root, it is loaded first.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    _env_path = PROJECT_ROOT / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class TelegramConfig:
    api_id: int
    api_hash: str
    phone: str
    session_path: Path


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"필수 환경변수 누락: {name}\n"
            f"다음 중 한 가지로 등록하세요:\n"
            f"  1) 셸: export {name}='값'\n"
            f"  2) .env 파일: {name}=값\n"
            f"그 후 source ~/.bashrc 또는 앱 재시작"
        )
    return val


def load_telegram_config() -> TelegramConfig:
    api_id_raw = _require("TG_API_ID")
    api_hash = _require("TG_API_HASH")
    phone = _require("TG_PHONE")
    try:
        api_id = int(api_id_raw)
    except ValueError as e:
        raise RuntimeError(f"TG_API_ID must be an integer, got: {api_id_raw!r}") from e
    return TelegramConfig(
        api_id=api_id,
        api_hash=api_hash,
        phone=phone,
        session_path=DATA_DIR / "market_radar.session",
    )


def load_llm_config() -> LLMConfig:
    return LLMConfig(
        base_url=os.getenv("TG_LLM_BASE_URL", "http://127.0.0.1:18085/v1"),
        api_key=os.getenv("TG_LLM_API_KEY", "not-needed"),
        model=os.getenv("TG_LLM_MODEL", "gemma-4-12b-agentic-v2"),
    )


def load_user_interests() -> list[str]:
    """Load user interests from data/user_interests.json (Phase 0: empty default)."""
    p = DATA_DIR / "user_interests.json"
    if not p.exists():
        return []
    try:
        import json

        with p.open(encoding="utf-8") as f:
            data = json.load(f)
        interests = data.get("user_interests", [])
        if isinstance(interests, list):
            return [str(x) for x in interests]
        return []
    except Exception:
        return []
