"""Fetchers for each applicant tracking system's public job API.

Every fetcher takes a board dict (from companies.discover) and returns a list
of normalized jobs:

    {"id", "company", "title", "location", "url", "ats", "posted"}

`posted` is an ISO-8601 string when the ATS provides one, otherwise None.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

USER_AGENT = "job-scanner/1.0 (+https://github.com)"
TIMEOUT = 20

# Search terms sent to boards that support server-side search (Workday and
# SmartRecruiters). Other boards return every job and are filtered locally.
SEARCH_TERMS = ["intern", "new grad", "entry level"]


def http_get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(url: str, payload: dict):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _job(board: dict, job_id, title, location, url, posted=None) -> dict:
    return {
        "id": f"{board['ats']}:{board.get('slug') or board.get('host')}:{job_id}",
        "company": board.get("company") or board.get("slug") or board.get("tenant", ""),
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "url": url,
        "ats": board["ats"],
        "posted": posted,
    }


def _ms_to_iso(ms) -> str | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------- parsers
# Parsers are separate from the network calls so they can be unit tested
# against saved sample responses.

def parse_greenhouse(board: dict, data: dict) -> list[dict]:
    return [
        _job(board, j["id"], j.get("title"), (j.get("location") or {}).get("name"),
             j.get("absolute_url"), j.get("first_published") or j.get("updated_at"))
        for j in data.get("jobs", [])
    ]


def parse_lever(board: dict, data: list) -> list[dict]:
    jobs = []
    for j in data:
        cats = j.get("categories") or {}
        location = cats.get("location") or ", ".join(cats.get("allLocations") or [])
        jobs.append(_job(board, j["id"], j.get("text"), location, j.get("hostedUrl"), _ms_to_iso(j.get("createdAt"))))
    return jobs


def parse_ashby(board: dict, data: dict) -> list[dict]:
    jobs = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        jobs.append(_job(board, j["id"], j.get("title"), j.get("location"),
                         j.get("jobUrl") or f"https://jobs.ashbyhq.com/{board['slug']}/{j['id']}",
                         j.get("publishedAt")))
    return jobs


def parse_smartrecruiters(board: dict, data: dict) -> list[dict]:
    jobs = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        location = loc.get("fullLocation") or ", ".join(
            x for x in [loc.get("city"), loc.get("region"), loc.get("country", "").upper()] if x
        )
        jobs.append(_job(board, j["id"], j.get("name"), location,
                         f"https://jobs.smartrecruiters.com/{board['slug']}/{j['id']}", j.get("releasedDate")))
    return jobs


def parse_workday(board: dict, data: dict) -> list[dict]:
    jobs = []
    for j in data.get("jobPostings", []):
        path = j.get("externalPath")
        if not path:
            continue
        url = f"https://{board['host']}/{board['site']}{path}"
        jobs.append(_job(board, path, j.get("title"), j.get("locationsText"), url))
    return jobs


# ---------------------------------------------------------------- fetchers

def fetch_greenhouse(board: dict) -> list[dict]:
    api = "boards-api.eu.greenhouse.io" if board.get("region") == "eu" else "boards-api.greenhouse.io"
    return parse_greenhouse(board, http_get_json(f"https://{api}/v1/boards/{board['slug']}/jobs"))


def fetch_lever(board: dict) -> list[dict]:
    return parse_lever(board, http_get_json(f"https://api.lever.co/v0/postings/{board['slug']}?mode=json"))


def fetch_ashby(board: dict) -> list[dict]:
    return parse_ashby(board, http_get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board['slug']}"))


def fetch_smartrecruiters(board: dict) -> list[dict]:
    jobs: dict[str, dict] = {}
    for term in SEARCH_TERMS:
        url = f"https://api.smartrecruiters.com/v1/companies/{board['slug']}/postings?limit=100&q={term.replace(' ', '%20')}"
        for job in parse_smartrecruiters(board, http_get_json(url)):
            jobs[job["id"]] = job
    return list(jobs.values())


def fetch_workday(board: dict) -> list[dict]:
    api = f"https://{board['host']}/wday/cxs/{board['tenant']}/{board['site']}/jobs"
    jobs: dict[str, dict] = {}
    for term in SEARCH_TERMS:
        data = http_post_json(api, {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": term})
        for job in parse_workday(board, data):
            jobs[job["id"]] = job
    return list(jobs.values())


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workday": fetch_workday,
}
