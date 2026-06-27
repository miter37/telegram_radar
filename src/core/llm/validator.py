"""JSON schema validator for LLM extractions."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft7Validator

# Korean-char length caps matching feed_extract_v0.1 prompt rules:
#   - topic: 12자 이내
#   - main_content: 50자 이내
TOPIC_MAX_CHARS = 12
MAIN_CONTENT_MAX_CHARS = 50

SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "datetime",
        "channel_name",
        "topic",
        "main_content",
        "tags",
        "tag_groups",
        "importance_score",
        "interest_score",
        "should_alert",
    ],
    "properties": {
        "datetime": {"type": "string", "minLength": 1},
        "channel_name": {"type": "string", "minLength": 1},
        "topic": {"type": "string", "minLength": 1, "maxLength": TOPIC_MAX_CHARS * 4},
        "main_content": {"type": "string", "minLength": 1, "maxLength": MAIN_CONTENT_MAX_CHARS * 4},
        "tags": {"type": "array", "items": {"type": "string"}},
        "tag_groups": {
            "type": "object",
            "properties": {
                "companies": {"type": "array", "items": {"type": "string"}},
                "people": {"type": "array", "items": {"type": "string"}},
                "industries": {"type": "array", "items": {"type": "string"}},
            },
        },
        "importance_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "interest_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "should_alert": {"type": "boolean"},
    },
    "additionalProperties": True,
}

_validator = Draft7Validator(SCHEMA)


def _truncate_korean(text: str, max_chars: int) -> str:
    """Truncate at Korean-char boundary (max_chars count of graphemes).

    Uses len() which counts code points; for CJK this is a close
    approximation of "characters". Falls back to hard slice if needed.
    """
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()


def validate(payload: dict) -> tuple[bool, str | None]:
    """Return (ok, first_error_message)."""
    errors = list(_validator.iter_errors(payload))
    if not errors:
        return True, None
    e = errors[0]
    path = "/".join(str(p) for p in e.absolute_path) or "<root>"
    return False, f"{path}: {e.message}"


def coerce(payload: dict) -> dict:
    """Best-effort coercion: clip scores, ensure list types, defaults,
    and enforce Korean-char length caps on topic (12) + main_content (50).
    """
    out = dict(payload)
    for key in ("importance_score", "interest_score"):
        try:
            v = int(out.get(key, 0))
        except (TypeError, ValueError):
            v = 0
        out[key] = max(0, min(100, v))
    out["should_alert"] = bool(out.get("should_alert", False))
    if not isinstance(out.get("tags"), list):
        out["tags"] = []
    tg = out.get("tag_groups") or {}
    if not isinstance(tg, dict):
        tg = {}
    for k in ("companies", "people", "industries"):
        v = tg.get(k)
        if not isinstance(v, list):
            tg[k] = []
    out["tag_groups"] = tg
    for k in ("topic", "main_content"):
        v = out.get(k)
        if not isinstance(v, str):
            out[k] = ""
    # Enforce prompt-declared length caps (Korean chars).
    if out.get("topic"):
        out["topic"] = _truncate_korean(out["topic"], TOPIC_MAX_CHARS)
    if out.get("main_content"):
        out["main_content"] = _truncate_korean(out["main_content"], MAIN_CONTENT_MAX_CHARS)
    return out
