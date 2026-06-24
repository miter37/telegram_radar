"""Tag normalization: 5-layer mapping (exact, alias, normalized, fuzzy, log new)."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

from ..db import repositories

logger = logging.getLogger(__name__)


# Built-in alias table (from the development plan)
BUILTIN_ALIASES: dict[str, tuple[str, str]] = {
    # alias -> (canonical_name, group)
    "하닉": ("SK하이닉스", "company"),
    "sk hynix": ("SK하이닉스", "company"),
    "sk하이닉스": ("SK하이닉스", "company"),
    "sk hynix": ("SK하이닉스", "company"),
    "젠슨": ("젠슨 황", "person"),
    "젠슨황": ("젠슨 황", "person"),
    "jensen huang": ("젠슨 황", "person"),
    "jensen": ("젠슨 황", "person"),
    "유리 기판": ("유리기판", "industry"),
    "글라스기판": ("유리기판", "industry"),
    "테스트소켓": ("반도체테스트", "industry"),
    "test socket": ("반도체테스트", "industry"),
    "hbm": ("HBM", "industry"),
    "동박": ("동박", "industry"),
    "구리포일": ("동박", "industry"),
    "삼성전자": ("삼성전자", "company"),
    "삼전": ("삼성전자", "company"),
    "skc": ("SKC", "company"),
    "삼성전기": ("삼성전기", "company"),
    "isc": ("ISC", "company"),
    "티에스이": ("티에스이", "company"),
    "리노공업": ("리노공업", "company"),
    "솔루스": ("솔루스첨단소재", "company"),
    "솔루스첨단소재": ("솔루스첨단소재", "company"),
    "롯데에너지": ("롯데에너지머티리얼즈", "company"),
    "ls 일렉트릭": ("LS ELECTRIC", "company"),
    "ls electric": ("LS ELECTRIC", "company"),
    "엔비디아": ("엔비디아", "company"),
    "nvidia": ("엔비디아", "company"),
    "데이터센터": ("데이터센터", "industry"),
    "전력기기": ("전력기기", "industry"),
    "변압기": ("변압기", "industry"),
    "ai서버": ("AI서버", "industry"),
    "ai 패키징": ("AI패키징", "industry"),
}


def _normalize_text(s: str) -> str:
    return re.sub(r"[\s\-_/.,]+", "", (s or "").strip().lower())


@dataclass
class NormalizedTag:
    canonical_id: int
    canonical_name: str
    group: str
    confidence: float
    matched_layer: str  # 'exact' | 'alias' | 'normalized' | 'fuzzy' | 'new'


class TagNormalizer:
    """Normalize a raw tag string to a canonical tag.

    Layers:
    1. exact match against canonical_tags.canonical_name
    2. alias match (built-in + DB canonical_tags.aliases)
    3. normalized (strip spaces/punct/case) match
    4. fuzzy similarity (>= 0.85) against canonical names
    5. else: log as new candidate, return existing or create with confidence 0
    """

    def __init__(self, conn: sqlite3.Connection, *, fuzzy_threshold: float = 0.85):
        self._conn = conn
        self._fuzzy_threshold = fuzzy_threshold
        self._alias_cache: Optional[dict[str, tuple[str, str]]] = None
        self._canon_cache: Optional[list[tuple[int, str, str]]] = None

    def _reload_caches(self) -> None:
        # canonical_tags
        rows = self._conn.execute(
            "SELECT id, canonical_name, tag_group FROM canonical_tags"
        ).fetchall()
        self._canon_cache = [(r["id"], r["canonical_name"], r["tag_group"]) for r in rows]
        # merge builtin aliases + DB aliases (all keys normalized)
        aliases: dict[str, tuple[str, str]] = {}
        for k, v in BUILTIN_ALIASES.items():
            aliases[_normalize_text(k)] = v
        for r in rows:
            raw = r["canonical_name"]
            group = r["tag_group"]
            # canonical name as its own alias
            aliases.setdefault(_normalize_text(raw), (raw, group))
            try:
                db_aliases = json.loads(
                    self._conn.execute(
                        "SELECT aliases FROM canonical_tags WHERE id = ?", (r["id"],)
                    ).fetchone()["aliases"] or "[]"
                )
                for a in db_aliases:
                    aliases.setdefault(_normalize_text(a), (raw, group))
            except Exception:
                pass
        self._alias_cache = aliases

    def _canon_by_name(self, name: str) -> Optional[tuple[int, str, str]]:
        for c in self._canon_cache or []:
            if c[1] == name:
                return c
        return None

    def _canon_by_id(self, cid: int) -> Optional[tuple[int, str, str]]:
        for c in self._canon_cache or []:
            if c[0] == cid:
                return c
        return None

    def normalize(self, raw: str, *, group_hint: Optional[str] = None) -> NormalizedTag:
        if not raw or not raw.strip():
            raise ValueError("empty tag")
        raw_clean = raw.strip()
        if self._alias_cache is None:
            self._reload_caches()

        # Layer 1: exact match on canonical_name
        exact = self._canon_by_name(raw_clean)
        if exact is not None:
            return NormalizedTag(
                canonical_id=exact[0],
                canonical_name=exact[1],
                group=exact[2],
                confidence=1.0,
                matched_layer="exact",
            )

        # Layer 2: alias (built-in or DB)
        norm = _normalize_text(raw_clean)
        alias_hit = self._alias_cache.get(norm)
        if alias_hit is not None:
            canon_name, alias_group = alias_hit
            hit = self._canon_by_name(canon_name)
            if hit is None:
                # register
                tag_id = repositories.upsert_canonical_tag(
                    self._conn, canonical_name=canon_name, tag_group=alias_group
                )
                self._reload_caches()
                hit = self._canon_by_name(canon_name)
            return NormalizedTag(
                canonical_id=hit[0],
                canonical_name=hit[1],
                group=hit[2],
                confidence=0.95,
                matched_layer="alias",
            )

        # Layer 3: normalized match against canonical names
        for cid, cname, cgroup in self._canon_cache or []:
            if _normalize_text(cname) == norm:
                return NormalizedTag(
                    canonical_id=cid,
                    canonical_name=cname,
                    group=cgroup,
                    confidence=0.9,
                    matched_layer="normalized",
                )

        # Layer 4: fuzzy
        best: Optional[tuple[float, tuple[int, str, str]]] = None
        for cid, cname, cgroup in self._canon_cache or []:
            score = SequenceMatcher(None, norm, _normalize_text(cname)).ratio()
            if best is None or score > best[0]:
                best = (score, (cid, cname, cgroup))
        if best is not None and best[0] >= self._fuzzy_threshold:
            cid, cname, cgroup = best[1]
            return NormalizedTag(
                canonical_id=cid,
                canonical_name=cname,
                group=cgroup,
                confidence=best[0],
                matched_layer="fuzzy",
            )

        # Layer 5: log new candidate, create with low confidence
        target_group = group_hint or "industry"
        existing = self._canon_by_name(raw_clean)
        if existing is not None:
            return NormalizedTag(
                canonical_id=existing[0],
                canonical_name=existing[1],
                group=existing[2],
                confidence=1.0,
                matched_layer="exact",
            )
        tag_id = repositories.upsert_canonical_tag(
            self._conn, canonical_name=raw_clean, tag_group=target_group
        )
        self._reload_caches()
        return NormalizedTag(
            canonical_id=tag_id,
            canonical_name=raw_clean,
            group=target_group,
            confidence=0.5,
            matched_layer="new",
        )

    def add_alias(self, canonical_name: str, alias: str) -> bool:
        """Append `alias` to the canonical_name entry's alias list."""
        row = self._conn.execute(
            "SELECT id, aliases FROM canonical_tags WHERE canonical_name = ?",
            (canonical_name,),
        ).fetchone()
        if row is None:
            return False
        try:
            aliases = json.loads(row["aliases"] or "[]")
        except Exception:
            aliases = []
        if alias not in aliases:
            aliases.append(alias)
        self._conn.execute(
            "UPDATE canonical_tags SET aliases = ? WHERE id = ?",
            (json.dumps(aliases, ensure_ascii=False), row["id"]),
        )
        self._conn.commit()
        self._reload_caches()
        return True
