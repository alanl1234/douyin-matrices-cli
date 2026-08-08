"""Shared helpers for the dashboard package."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    """UTC timestamp in ISO-8601 (used as a stable, monotonic-ish marker)."""
    return datetime.now(timezone.utc).isoformat()


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def json_loads(text: Any, default: Any = None) -> Any:
    if text is None:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def split_terms(items: Any) -> list[str]:
    """Flatten a list of strings / nested lists into cleaned tokens.

    Used to normalise persona topics / forbidden words for matching.
    """
    out: list[str] = []
    if items is None:
        return out
    if isinstance(items, str):
        items = [items]
    for it in items:
        if isinstance(it, str):
            out.extend(t for t in re.split(r"[\s,，、;；]+", it) if t)
        elif isinstance(it, (list, tuple, set)):
            out.extend(split_terms(list(it)))
    return [t.strip() for t in out if t and t.strip()]
