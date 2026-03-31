"""Persist last-indexed timestamps and auxiliary index state."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codebase_assistant.config import STATE_PATH


def _load() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"repos": {}}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"repos": {}}


def _save(data: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def touch_repo_index(repo: str, iso_ts: str | None = None) -> None:
    data = _load()
    data.setdefault("repos", {})[repo] = {
        "last_indexed_at": iso_ts or datetime.now(timezone.utc).isoformat(),
    }
    _save(data)


def touch_org_index(org: str) -> None:
    data = _load()
    data["org_last_indexed_at"] = datetime.now(timezone.utc).isoformat()
    data["last_org"] = org
    _save(data)


def get_repo_last_indexed(repo: str) -> str | None:
    r = _load().get("repos", {}).get(repo)
    return r.get("last_indexed_at") if r else None


def get_global_summary() -> dict[str, Any]:
    return _load()
