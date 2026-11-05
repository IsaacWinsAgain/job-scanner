"""Discover company job boards from the SimplifyJobs listing data.

SimplifyJobs publishes thousands of internship and new-grad postings. Each
posting links to the company's applicant tracking system (ATS). By parsing
those links we learn which board every company uses, which gives us a list
of 2,000+ company boards to poll directly, without maintaining it by hand.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse, parse_qs

LISTING_SOURCES = [
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
]

_SLUG = r"[A-Za-z0-9_.-]+"
_LOCALE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


def board_from_url(url: str) -> tuple[str, dict] | None:
    """Return (ats, board) for a job URL, or None if the ATS isn't supported.

    The board dict holds everything a fetcher needs to call that ATS's API.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    parts = [p for p in parsed.path.split("/") if p]

    if host.endswith("greenhouse.io"):
        region = "eu" if ".eu." in host else "us"
        if parts[:2] == ["embed", "job_app"]:
            slug = parse_qs(parsed.query).get("for", [None])[0]
        else:
            slug = parts[0] if parts else None
        if slug and re.fullmatch(_SLUG, slug):
            return "greenhouse", {"slug": slug.lower(), "region": region}

    elif host == "jobs.lever.co" and parts:
        return "lever", {"slug": parts[0].lower()}

    elif host == "jobs.ashbyhq.com" and parts:
        return "ashby", {"slug": parts[0]}

    elif host == "jobs.smartrecruiters.com" and parts:
        return "smartrecruiters", {"slug": parts[0]}

    elif host.endswith(".myworkdayjobs.com") and parts:
        if _LOCALE.match(parts[0]):
            parts = parts[1:]
        if parts and parts[0] != "job":
            return "workday", {"host": host, "tenant": host.split(".")[0], "site": parts[0]}

    return None


def board_key(ats: str, board: dict) -> str:
    """A stable, unique id for a board, used to de-duplicate."""
    if ats == "workday":
        return f"workday:{board['host']}/{board['site']}"
    return f"{ats}:{board.get('region', '')}:{board['slug'].lower()}"


def discover(listings: list[dict]) -> list[dict]:
    """Build a de-duplicated list of boards from SimplifyJobs listings."""
    boards: dict[str, dict] = {}
    for item in listings:
        found = board_from_url(item.get("url", ""))
        if not found:
            continue
        ats, board = found
        key = board_key(ats, board)
        if key not in boards:
            boards[key] = {"ats": ats, "company": (item.get("company_name") or "").strip(), **board}
    return sorted(boards.values(), key=lambda b: (b["ats"], b["company"].lower()))


def load_listings(fetch_text) -> list[dict]:
    """Download and merge every SimplifyJobs listing file."""
    merged: list[dict] = []
    for url in LISTING_SOURCES:
        merged.extend(json.loads(fetch_text(url)))
    return merged
