"""Topic report prompt builders (Stage 1 clustering + Stage 2 summary)."""

from __future__ import annotations

import json
from typing import Optional

from .prompts import get_prompt, list_prompt_files

CLUSTER_PROMPT_VERSION = "topic_cluster_v0.1"
SUMMARY_PROMPT_VERSION = "topic_summary_v0.1"


def _ensure_prompts_loaded() -> None:
    """Pre-load prompt files into cache so we can read them."""
    try:
        get_prompt(CLUSTER_PROMPT_VERSION)
        get_prompt(SUMMARY_PROMPT_VERSION)
    except FileNotFoundError:
        # prompts.py is responsible for raising; we let it propagate
        raise


def render_cluster_prompt(
    *,
    date: str,
    messages: list[dict],
    min_topics: int = 5,
    max_topics: int = 20,
) -> str:
    template = get_prompt(CLUSTER_PROMPT_VERSION)
    return (
        template
        .replace("{{date}}", date)
        .replace("{{messages_json}}", json.dumps(messages, ensure_ascii=False)[:18000])
        .replace("{{min_topics}}", str(min_topics))
        .replace("{{max_topics}}", str(max_topics))
    )


def render_summary_prompt(
    *,
    date: str,
    label: str,
    members: list[dict],
) -> str:
    template = get_prompt(SUMMARY_PROMPT_VERSION)
    return (
        template
        .replace("{{date}}", date)
        .replace("{{label}}", label)
        .replace("{{members_json}}", json.dumps(members, ensure_ascii=False)[:18000])
    )


def split_for_chat(rendered: str) -> tuple[str, str]:
    """Split rendered prompt into (system, user) for chat completions.

    Strips {SYSTEM}...{INPUT}...{OUTPUT_JSON_SCHEMA} markers.
    """
    sys_start = rendered.find("{SYSTEM}")
    inp_start = rendered.find("{INPUT}")
    out_start = rendered.find("{OUTPUT_JSON_SCHEMA}")
    if sys_start < 0 or inp_start < 0 or out_start < 0:
        # no markers → return as-is
        return rendered, ""
    system = rendered[sys_start + len("{SYSTEM}"):inp_start].strip()
    # include schema hint in system so model knows shape
    schema = rendered[out_start + len("{OUTPUT_JSON_SCHEMA}"):].strip()
    if schema:
        system = system + "\n\n" + schema
    user_payload = rendered[inp_start + len("{INPUT}"):out_start].strip()
    return system, user_payload
