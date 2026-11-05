"""Job scanner entry point: python -m scanner.main

One run:
  1. Load the list of company boards (refreshed from SimplifyJobs daily).
  2. Fetch every board's open jobs in parallel.
  3. Keep internship and new-grad roles that match config.json.
  4. Notify about jobs not seen in an earlier run, then save state.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from . import companies, notify, sources, state as state_mod
from .filters import JobFilter

BOARD_REFRESH = timedelta(hours=24)


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": sources.USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def get_boards(state: dict, config: dict, log) -> list[dict]:
    """Return the board list, refreshing it from SimplifyJobs once a day."""
    cached = state["boards"]
    fresh_enough = cached.get("updated") and (
        datetime.now(timezone.utc) - datetime.fromisoformat(cached["updated"]) < BOARD_REFRESH
    )
    if not fresh_enough:
        try:
            boards = companies.discover(companies.load_listings(fetch_text))
            state["boards"] = {"updated": state_mod.now_iso(), "list": boards}
            log(f"Refreshed company list: {len(boards)} boards")
        except Exception as exc:  # keep using yesterday's list if GitHub is unreachable
            log(f"Could not refresh company list ({exc}); using cached list")
    boards = list(state["boards"].get("list", []))
    known = {companies.board_key(b["ats"], b) for b in boards}
    for extra in config.get("extra_boards", []):
        if companies.board_key(extra["ats"], extra) not in known:
            boards.append(extra)
    return boards


def scan(boards: list[dict], workers: int, log) -> tuple[dict[str, list[dict]], list[str]]:
    """Fetch all boards in parallel. Returns ({board_key: jobs}, failures)."""
    results: dict[str, list[dict]] = {}
    failures: list[str] = []

    def run(board):
        return sources.FETCHERS[board["ats"]](board)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run, b): b for b in boards if b["ats"] in sources.FETCHERS}
        for future in as_completed(futures):
            board = futures[future]
            key = companies.board_key(board["ats"], board)
            try:
                results[key] = future.result()
            except Exception as exc:
                failures.append(f"{key}: {type(exc).__name__}")
    return results, failures


def is_recent(job: dict, max_age_days: int) -> bool:
    """False only when the ATS gives a posting date older than max_age_days."""
    if not job.get("posted") or not max_age_days:
        return True
    try:
        posted = datetime.fromisoformat(job["posted"].replace("Z", "+00:00"))
    except ValueError:
        return True
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - posted <= timedelta(days=max_age_days)


def select_new(results, state, job_filter, max_age_days):
    """Split matching jobs into (new jobs to notify about, all matching ids).

    Jobs from a board we've never scanned before are recorded silently, so a
    newly discovered company doesn't trigger alerts for all its old postings.
    """
    known_boards = set(state["known_boards"])
    new_jobs, matched_ids = [], []
    for key, jobs in results.items():
        first_time = key not in known_boards
        for job in jobs:
            role = job_filter.role_type(job)
            if not role:
                continue
            job["role_type"] = role
            matched_ids.append(job["id"])
            if (state["seeded"] and not first_time and job["id"] not in state["seen"]
                    and is_recent(job, max_age_days)):
                new_jobs.append(job)
        known_boards.add(key)
    state["known_boards"] = sorted(known_boards)
    return new_jobs, matched_ids


def write_step_summary(new_jobs, boards, failures, elapsed):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"## {len(new_jobs)} new jobs", "",
             f"Scanned {len(boards)} boards in {elapsed:.0f}s ({len(failures)} failed).", ""]
    if new_jobs:
        lines += ["| Type | Company | Title | Location |", "|---|---|---|---|"]
        for j in new_jobs:
            label = notify.ROLE_LABEL.get(j["role_type"], "")
            title = j["title"].replace("|", "/")
            lines.append(f"| {label} | {j['company']} | [{title}]({j['url']}) | {j['location'].replace('|', '/')} |")
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def run_url() -> str | None:
    server, repo, run_id = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Scan company job boards for new roles.")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--state", default="state/state.json")
    parser.add_argument("--dry-run", action="store_true", help="print new jobs instead of notifying")
    parser.add_argument("--limit", type=int, default=0, help="only scan the first N boards (for testing)")
    args = parser.parse_args(argv)
    log = lambda msg: print(msg, flush=True)

    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)
    topic = os.environ.get("NTFY_TOPIC")
    if not topic and not args.dry_run:
        log("NTFY_TOPIC is not set. Add it as a repository secret, or use --dry-run.")
        return 1

    state = state_mod.load(args.state)
    boards = get_boards(state, config, log)
    if args.limit:
        boards = boards[: args.limit]

    start = time.monotonic()
    results, failures = scan(boards, config.get("workers", 32), log)
    elapsed = time.monotonic() - start

    first_run = not state["seeded"]
    new_jobs, matched_ids = select_new(results, state, JobFilter(config), config.get("max_age_days", 7))
    new_jobs.sort(key=lambda j: (j["role_type"], j["company"].lower()))

    log(f"Scanned {len(results)}/{len(boards)} boards in {elapsed:.0f}s; "
        f"{len(matched_ids)} matching jobs, {len(new_jobs)} new, {len(failures)} boards failed")
    for job in new_jobs:
        log(f"  NEW [{job['role_type']}] {job['company']} — {job['title']} — {job['location']} — {job['url']}")

    if not args.dry_run:
        if first_run:
            notify.send_text(topic, "Job scanner is live",
                             f"Watching {len(results)} company boards ({len(matched_ids)} matching jobs right now). "
                             "You'll get a notification for each new posting.")
        else:
            notify.notify(topic, new_jobs, run_url())

    state_mod.mark_seen(state, matched_ids)
    state["seeded"] = True
    dropped = state_mod.prune(state)
    state_mod.save(args.state, state)
    write_step_summary(new_jobs, boards, failures, elapsed)
    if dropped:
        log(f"Forgot {dropped} jobs that have been closed for 14+ days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
