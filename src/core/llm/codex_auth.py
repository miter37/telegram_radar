"""Codex OAuth: read & refresh ~/.codex/auth.json automatically.

Format (from Codex CLI):
{
  "auth_mode": "chatgpt",
  "OPENAI_API_KEY": null,
  "tokens": {
    "id_token": "<JWT with 'exp'>",
    "access_token": "<JWT>",
    "refresh_token": "rt.1...",
    "account_id": "..."
  },
  "last_refresh": "2026-06-19T11:50:11.423822207Z"
}

Refresh endpoint: https://auth.openai.com/oauth/token
Payload: grant_type=refresh_token, refresh_token=<rt>, client_id=app_EMoamEEZ73f0CkXaXp7hrann
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Codex CLI client_id (public, embedded in codex binary)
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_REFRESH_URL = "https://auth.openai.com/oauth/token"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"  # alias

# Auth file search paths (in order of preference)
AUTH_PATHS = [
    Path.home() / ".codex" / "auth.json",
    Path.home() / ".config" / "openai" / "auth.json",
    Path("/root/.codex/auth.json"),
]


@dataclass
class CodexTokens:
    access_token: str
    refresh_token: str
    id_token: str
    account_id: str
    expires_at: float   # epoch seconds
    last_refresh_iso: str

    def is_expired(self, *, skew_seconds: int = 60) -> bool:
        """True if the token expires within skew_seconds."""
        return time.time() >= (self.expires_at - skew_seconds)


def find_auth_file() -> Optional[Path]:
    for p in AUTH_PATHS:
        if p.exists():
            return p
    return None


def read_auth_json(path: Optional[Path] = None) -> Optional[dict]:
    p = path or find_auth_file()
    if p is None:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("read auth.json failed (%s): %s", p, e)
        return None


def _parse_id_token_exp(id_token: str) -> Optional[float]:
    """Parse the `exp` claim from a JWT (no signature verification)."""
    try:
        parts = id_token.split(".")
        if len(parts) < 2:
            return None
        import base64
        payload = parts[1]
        # pad for base64
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return float(data.get("exp")) if "exp" in data else None
    except Exception:
        return None


def _parse_last_refresh(last_refresh: str) -> float:
    """Parse '2026-06-19T11:50:11.423822207Z' or ISO."""
    if not last_refresh:
        return 0.0
    s = last_refresh.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        return dt.timestamp()
    except Exception:
        return 0.0


def extract_tokens(data: dict) -> Optional[CodexTokens]:
    tokens = data.get("tokens") or {}
    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    if not access:
        return None
    id_token = tokens.get("id_token") or ""
    expires = _parse_id_token_exp(id_token)
    if expires is None:
        # fallback: assume 1 hour from last_refresh
        last = _parse_last_refresh(data.get("last_refresh") or "")
        expires = (last + 3600.0) if last > 0 else (time.time() + 3600.0)
    return CodexTokens(
        access_token=access,
        refresh_token=refresh or "",
        id_token=id_token,
        account_id=tokens.get("account_id") or "",
        expires_at=expires,
        last_refresh_iso=data.get("last_refresh") or "",
    )


async def refresh_codex_tokens(
    refresh_token: str,
    *,
    timeout: float = 15.0,
) -> Optional[dict]:
    """Call Codex refresh endpoint. Returns new token dict on success."""
    import httpx
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CODEX_CLIENT_ID,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                CODEX_REFRESH_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("codex token refresh failed: %s", e)
        return None


def write_auth_json(path: Path, data: dict) -> None:
    """Atomic write of auth.json."""
    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(
        prefix=".auth.", suffix=".json", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def apply_refreshed_tokens(
    data: dict,
    refreshed: dict,
) -> dict:
    """Merge refreshed tokens into the auth.json structure."""
    new_id = refreshed.get("id_token") or data.get("tokens", {}).get("id_token", "")
    new_access = refreshed.get("access_token")
    new_refresh = refreshed.get("refresh_token", data.get("tokens", {}).get("refresh_token", ""))
    if new_access:
        data.setdefault("tokens", {})
        data["tokens"]["access_token"] = new_access
    if new_refresh:
        data["tokens"]["refresh_token"] = new_refresh
    if new_id:
        data["tokens"]["id_token"] = new_id
    data["last_refresh"] = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    return data


# ---------- High-level helpers ----------

class CodexAuth:
    """Cached Codex access token with auto-refresh."""

    def __init__(self, path: Optional[Path] = None):
        self._path = path or find_auth_file()
        self._cached: Optional[CodexTokens] = None
        self._refreshed_at: float = 0.0
        self._refresh_lock = False

    def get(self) -> Optional[str]:
        """Return a valid access_token, refreshing if needed. None if unavailable."""
        if self._path is None or not self._path.exists():
            return None
        data = read_auth_json(self._path)
        if data is None:
            return None
        tokens = extract_tokens(data)
        if tokens is None:
            return None
        if tokens.is_expired() and tokens.refresh_token:
            # try refresh
            try:
                import asyncio
                refreshed = asyncio.run(refresh_codex_tokens(tokens.refresh_token))
            except Exception:
                refreshed = None
            if refreshed:
                data = apply_refreshed_tokens(data, refreshed)
                try:
                    write_auth_json(self._path, data)
                except Exception as e:
                    logger.warning("write auth.json failed: %s", e)
                tokens = extract_tokens(data) or tokens
            else:
                logger.warning("codex refresh failed; using existing access_token (may be expired)")
        return tokens.access_token if tokens else None

    def get_with_meta(self) -> Optional[CodexTokens]:
        """Return full token info, refreshing if needed."""
        if self._path is None or not self._path.exists():
            return None
        data = read_auth_json(self._path)
        if data is None:
            return None
        tokens = extract_tokens(data)
        if tokens is None:
            return None
        if tokens.is_expired() and tokens.refresh_token:
            try:
                import asyncio
                refreshed = asyncio.run(refresh_codex_tokens(tokens.refresh_token))
            except Exception:
                refreshed = None
            if refreshed:
                data = apply_refreshed_tokens(data, refreshed)
                try:
                    write_auth_json(self._path, data)
                except Exception as e:
                    logger.warning("write auth.json failed: %s", e)
                tokens = extract_tokens(data) or tokens
        return tokens


# Module-level singleton (lazy)
_singleton: Optional[CodexAuth] = None


def get_codex_token() -> Optional[str]:
    """Module-level helper: returns current access_token (refreshes if needed)."""
    global _singleton
    if _singleton is None:
        _singleton = CodexAuth()
    return _singleton.get()
