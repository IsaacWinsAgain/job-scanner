# Job Scanner

Watches **3,800+ company job boards** (3,500+ companies) and sends a push notification to your phone within minutes of a new **internship** or **new-grad / entry-level** posting going live.

It reads the companies' hiring systems directly (Greenhouse, Lever, Ashby, SmartRecruiters and Workday), so alerts arrive as soon as a company publishes a role, often before it shows up on job sites.

## How it works

```
SimplifyJobs listings ──► discover company boards (daily)
                               │
            every 10 min       ▼
GitHub Actions ──► fetch 3,800+ boards in parallel ──► filter titles (config.json)
                                                             │
                     saved state (already-seen jobs) ◄───────┤
                                                             ▼
                                              new job? ──► ntfy push to phone
```

- **Company discovery.** Every posting in the [SimplifyJobs](https://github.com/SimplifyJobs) internship and new-grad lists links to the company's hiring system. `scanner/companies.py` turns those links into a list of boards to poll, refreshed once a day, so new companies are picked up automatically.
- **Fetching.** `scanner/sources.py` calls each system's public job API, 32 boards at a time.
- **Filtering.** `scanner/filters.py` keeps titles that match a role type and a tech field, and drops senior, manager and PhD roles. Everything is configurable in `config.json`.
- **No duplicate alerts.** `scanner/state.py` remembers which jobs it has already seen, saved between runs with the GitHub Actions cache. Companies discovered for the first time are recorded silently, and postings older than 7 days are skipped, so you only hear about genuinely new roles.
- **Notifications.** `scanner/notify.py` sends one push per new job (tap it to open the application). If more than 10 arrive at once, it sends one summary that links to the full list.

## Setup (about 10 minutes)

### 1. Set up phone notifications
1. Install the free **ntfy** app (iPhone App Store or Google Play).
2. Tap **+** and subscribe to a topic with a hard-to-guess name, for example `isaac-jobs-998b115c`. Anyone who knows the name can read it, so keep it private.

### 2. Create the GitHub repo
1. On GitHub, create a new **public** repository named `job-scanner`, and check **Add a README** so `main` exists.
   Public repos get unlimited free Actions minutes. A private repo gets 2,000 minutes a month, which this would use up.
2. Clone it and add the code on a branch:
   ```bash
   git clone https://github.com/<your-username>/job-scanner.git
   cd job-scanner
   git checkout -b feature/job-scanner
   # copy this project's files into the folder, replacing the README
   git add .
   git commit -m "Add job scanner"
   git push -u origin feature/job-scanner
   ```
3. Open a pull request from `feature/job-scanner` into `main`. The **Tests** workflow runs on it. Merge once it passes.

### 3. Add your notification topic
Repo **Settings → Secrets and variables → Actions → New repository secret**
- Name: `NTFY_TOPIC`
- Value: your topic name, e.g. `isaac-jobs-998b115c`

### 4. Start it
Go to **Actions → Scan job boards → Run workflow**. Within a few minutes your phone gets a "Job scanner is live" message. After that it runs every 10 minutes on its own.

## Customizing

Edit `config.json` (on a branch, through a pull request):

| Setting | What it does |
|---|---|
| `role_types` | Title patterns for each role type. A job must match one. |
| `fields` | Title must also match one of these (software, security, data...). Use `[]` to allow any field. |
| `exclude` | Titles matching any of these are dropped (senior, manager, PhD...). |
| `locations` | Leave `[]` for anywhere, or limit it, e.g. `["United States", "Remote", "NC", "NY"]`. |
| `max_age_days` | Skip postings older than this when the board reports a date. |
| `extra_boards` | Add companies by hand, e.g. `{"ats": "greenhouse", "slug": "stripe", "region": "us", "company": "Stripe"}`. |

Patterns are case-insensitive regular expressions matched on whole words.

## Running locally

Needs Python 3.10+ and no extra packages.

```bash
python -m unittest discover -s tests -v          # run the tests
python -m scanner.main --dry-run --limit 100     # scan 100 boards and print, without notifying
```

## Notes

- GitHub can delay scheduled runs by a few minutes when it's busy, so expect alerts within roughly 10–20 minutes of a posting.
- GitHub pauses scheduled workflows in a repo with no activity for 60 days. If alerts stop, re-enable the workflow from the Actions tab.
- Title filters can't catch every entry-level role (some new-grad jobs are just titled "Software Engineer"). The SimplifyJobs lists, which are hand-tagged, are a good complement.
