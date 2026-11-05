"""Remember which jobs have already been seen, between runs.

The state file is small JSON, persisted between GitHub Actions runs with the
Actions cache:

    {
      "seeded": true,
      "known_boards": ["greenhouse:us:stripe", ...],
      "boards": {"updated": "<iso>", "list": [...]},
      "seen": {"<job id>": {"first_seen": "<iso>", "last_seen": "<iso>"}}
    }
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

# Forget a job this long after it stops appearing, so the file stays small.
FORGET_AFTER = timedelta(days=14)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(path: str) -> dict:
    if not os.path.exists(path):
        return {"seeded": False, "known_boards": [], "boards": {}, "seen": {}}
    with open(path, encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("seeded", False)
    state.setdefault("known_boards", [])
    state.setdefault("boards", {})
    state.setdefault("seen", {})
    return state


def save(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, separators=(",", ":"))
    os.replace(tmp, path)


def mark_seen(state: dict, job_ids, when: str | None = None) -> None:
    when = when or now_iso()
    seen = state["seen"]
    for job_id in job_ids:
        entry = seen.setdefault(job_id, {"first_seen": when})
        entry["last_seen"] = when


def prune(state: dict, now: datetime | None = None) -> int:
    """Drop jobs not seen for FORGET_AFTER. Returns how many were dropped."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - FORGET_AFTER
    stale = [
        job_id for job_id, entry in state["seen"].items()
        if datetime.fromisoformat(entry.get("last_seen", entry["first_seen"])) < cutoff
    ]
    for job_id in stale:
        del state["seen"][job_id]
    return len(stale)
