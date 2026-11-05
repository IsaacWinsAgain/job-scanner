"""Send phone notifications through ntfy (https://ntfy.sh).

ntfy is a free push service: install the ntfy app, subscribe to a private
topic name, and anything published to that topic shows up on your phone.
We publish JSON (instead of HTTP headers) so job titles with accents or
emoji are sent correctly.
"""

from __future__ import annotations

import json
import os
import urllib.request

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")

# Above this many new jobs in one run, send one summary instead of a
# notification per job, so a burst of postings doesn't flood your phone.
MAX_INDIVIDUAL = 10

ROLE_LABEL = {"internship": "Internship", "full_time": "Full-time"}


def _publish(topic: str, message: dict) -> None:
    body = json.dumps({"topic": topic, **message}).encode("utf-8")
    req = urllib.request.Request(
        NTFY_SERVER, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=20):
        pass


def format_job(job: dict) -> dict:
    label = ROLE_LABEL.get(job.get("role_type"), "Job")
    lines = [job["title"]]
    if job.get("location"):
        lines.append(job["location"])
    return {
        "title": f"{label} · {job['company']}",
        "message": "\n".join(lines),
        "click": job["url"],
        "tags": ["briefcase"],
        "actions": [{"action": "view", "label": "Apply", "url": job["url"]}],
    }


def format_summary(jobs: list[dict], details_url: str | None) -> dict:
    counts: dict[str, int] = {}
    for job in jobs:
        label = ROLE_LABEL.get(job.get("role_type"), "Job")
        counts[label] = counts.get(label, 0) + 1
    breakdown = ", ".join(f"{n} {label}" for label, n in counts.items())
    preview = "\n".join(f"• {j['company']} — {j['title']}" for j in jobs[:8])
    more = f"\n…and {len(jobs) - 8} more" if len(jobs) > 8 else ""
    message = {
        "title": f"{len(jobs)} new jobs ({breakdown})",
        "message": preview + more,
        "tags": ["briefcase"],
    }
    if details_url:
        message["click"] = details_url
    return message


def notify(topic: str, jobs: list[dict], details_url: str | None = None) -> int:
    """Send notifications for new jobs. Returns how many messages were sent."""
    if not jobs:
        return 0
    if len(jobs) <= MAX_INDIVIDUAL:
        for job in jobs:
            _publish(topic, format_job(job))
        return len(jobs)
    _publish(topic, format_summary(jobs, details_url))
    return 1


def send_text(topic: str, title: str, message: str) -> None:
    _publish(topic, {"title": title, "message": message, "tags": ["white_check_mark"]})
