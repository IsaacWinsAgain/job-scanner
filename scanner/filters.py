"""Decide which jobs are worth a notification, based on config.json."""

from __future__ import annotations

import re


def _compile(words: list[str]) -> re.Pattern | None:
    if not words:
        return None
    return re.compile(r"\b(?:" + "|".join(words) + r")\b", re.IGNORECASE)


class JobFilter:
    """Matches jobs by title (role type + field) and, optionally, location.

    A job passes when its title:
      * matches at least one `internship` or `full_time` pattern, and
      * matches at least one `fields` pattern (if any are set), and
      * matches none of the `exclude` patterns,
    and its location matches a `locations` pattern (if any are set).
    """

    def __init__(self, config: dict):
        role_types = config.get("role_types", {})
        self.role_patterns = {name: _compile(words) for name, words in role_types.items() if words}
        self.fields = _compile(config.get("fields", []))
        self.exclude = _compile(config.get("exclude", []))
        self.locations = _compile(config.get("locations", []))

    def role_type(self, job: dict) -> str | None:
        """Return the role type name ("internship", "full_time", ...) or None."""
        title = job.get("title", "")
        if self.exclude and self.exclude.search(title):
            return None
        if self.fields and not self.fields.search(title):
            return None
        if self.locations and not self.locations.search(job.get("location", "")):
            return None
        for name, pattern in self.role_patterns.items():
            if pattern.search(title):
                return name
        return None
