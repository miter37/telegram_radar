"""LLM prompt template loader."""

from __future__ import annotations

import json
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"

CURRENT_VERSION = "feed_extract_v0.1"

_PROMPT_CACHE: dict[str, str] = {}


def list_prompt_files() -> list[str]:
    """Return all prompt version filenames (without .txt)."""
    if not _PROMPTS_DIR.exists():
        return []
    return sorted(p.stem for p in _PROMPTS_DIR.glob("*.txt"))


def get_prompt(version: str = CURRENT_VERSION) -> str:
    if version not in _PROMPT_CACHE:
        p = _PROMPTS_DIR / f"{version}.txt"
        _PROMPT_CACHE[version] = p.read_text(encoding="utf-8")
    return _PROMPT_CACHE[version]


def render_prompt(
    *,
    version: str,
    datetime: str,
    channel_name: str,
    message_text: str,
    message_url: str | None,
    user_interests: list[str],
) -> str:
    """Fill the {INPUT} placeholder of the prompt template."""
    template = get_prompt(version)
    interests_json = json.dumps(user_interests, ensure_ascii=False)
    input_block = (
        "{\n"
        f'  "datetime": "{datetime}",\n'
        f'  "channel_name": "{channel_name}",\n'
        f'  "message_text": {json.dumps(message_text, ensure_ascii=False)},\n'
        f'  "message_url": {json.dumps(message_url)},\n'
        f'  "user_interests": {interests_json}\n'
        "}"
    )
    return template.replace("{{datetime}}", datetime) \
                    .replace("{{channel_name}}", channel_name) \
                    .replace("{{message_text}}", json.dumps(message_text, ensure_ascii=False)) \
                    .replace("{{message_url}}", json.dumps(message_url) if message_url else "null") \
                    .replace("{{user_interests_json}}", interests_json) \
                    .replace("{{INPUT}}\n{", "{INPUT}\n" + input_block)


def split_system_and_input(rendered: str) -> tuple[str, str]:
    """Split into (system_prompt, user_payload) for chat completions API.

    The template uses {SYSTEM} ... {INPUT} ... {OUTPUT_JSON_SCHEMA}.
    We send the whole thing as the system prompt; the user message is the
    filled input block only. This makes the model treat the schema as
    constraints rather than literal output.
    """
    sys_start = rendered.find("{SYSTEM}")
    inp_start = rendered.find("{INPUT}")
    out_start = rendered.find("{OUTPUT_JSON_SCHEMA}")
    system = rendered[sys_start + len("{SYSTEM}"):inp_start].strip()
    if out_start > 0:
        # include schema in system so the model knows the shape
        system = system + "\n\n" + rendered[out_start + len("{OUTPUT_JSON_SCHEMA}"):].strip()
    # Extract the JSON object after {INPUT}
    after = rendered[inp_start + len("{INPUT}"):].strip()
    user_payload = after.split("{OUTPUT_JSON_SCHEMA}")[0].strip()
    return system, user_payload
