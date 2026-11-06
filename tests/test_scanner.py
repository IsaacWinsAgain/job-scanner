"""Unit tests. Run with: python -m unittest discover -s tests -v"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from scanner import companies, main, notify, sources, state as state_mod
from scanner.filters import JobFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
    CONFIG = json.load(f)
CONFIG["locations"] = []  # filter tests check titles; test_location_filter sets its own locations

NOW = datetime.now(timezone.utc)
RECENT = (NOW - timedelta(hours=1)).isoformat()
OLD = (NOW - timedelta(days=30)).isoformat()

# Sample API responses, trimmed to the fields the scanner reads.
GREENHOUSE = {"jobs": [
    {"id": 101, "title": "Software Engineer Intern, Summer 2027", "location": {"name": "New York, NY"},
     "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/101", "first_published": RECENT},
    {"id": 102, "title": "Senior Software Engineer", "location": {"name": "Remote"},
     "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/102", "first_published": RECENT},
]}
LEVER = [
    {"id": "abc-1", "text": "New Grad Software Engineer", "hostedUrl": "https://jobs.lever.co/beta/abc-1",
     "categories": {"location": "Austin, TX", "commitment": "Full-time"},
     "createdAt": int((NOW - timedelta(hours=2)).timestamp() * 1000)},
]
ASHBY = {"jobs": [
    {"id": "x1", "title": "Security Engineering Intern", "location": "Remote - US",
     "jobUrl": "https://jobs.ashbyhq.com/gamma/x1", "publishedAt": RECENT, "isListed": True},
    {"id": "x2", "title": "Hidden Intern", "location": "Remote", "isListed": False},
]}
SMARTRECRUITERS = {"content": [
    {"id": "744", "name": "IT Intern", "releasedDate": RECENT,
     "location": {"city": "Raleigh", "region": "NC", "country": "us"}},
]}
WORKDAY = {"jobPostings": [
    {"title": "Associate Software Engineer", "externalPath": "/job/Raleigh-NC/Associate-Software-Engineer_R123",
     "locationsText": "Raleigh, NC", "postedOn": "Posted Today"},
]}


class CompanyDiscoveryTests(unittest.TestCase):
    def test_board_from_url(self):
        cases = {
            "https://job-boards.greenhouse.io/stripe/jobs/123": ("greenhouse", {"slug": "stripe", "region": "us"}),
            "https://boards.greenhouse.io/embed/job_app?for=gemini&token=7": ("greenhouse", {"slug": "gemini", "region": "us"}),
            "https://job-boards.eu.greenhouse.io/speechmatics/jobs/4": ("greenhouse", {"slug": "speechmatics", "region": "eu"}),
            "https://jobs.lever.co/Palantir/abc": ("lever", {"slug": "palantir"}),
            "https://jobs.ashbyhq.com/skydio/95f0/application": ("ashby", {"slug": "skydio"}),
            "https://jobs.smartrecruiters.com/WesternDigital/744": ("smartrecruiters", {"slug": "WesternDigital"}),
            "https://otis.wd5.myworkdayjobs.com/en-US/REC_Ext_Gateway/job/CT/Intern_1": (
                "workday", {"host": "otis.wd5.myworkdayjobs.com", "tenant": "otis", "site": "REC_Ext_Gateway"}),
            "https://caci.wd1.myworkdayjobs.com/external/job/MD/Intern_2": (
                "workday", {"host": "caci.wd1.myworkdayjobs.com", "tenant": "caci", "site": "external"}),
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(companies.board_from_url(url), expected)

    def test_unsupported_urls(self):
        for url in ["https://boards.greenhouse.io/embed/job_app?token=7", "https://www.tesla.com/careers/1", "not a url"]:
            self.assertIsNone(companies.board_from_url(url))

    def test_discover_dedupes_and_names(self):
        listings = [
            {"company_name": " Stripe ", "url": "https://job-boards.greenhouse.io/stripe/jobs/1"},
            {"company_name": "Stripe", "url": "https://boards.greenhouse.io/Stripe/jobs/2"},
            {"company_name": "Palantir", "url": "https://jobs.lever.co/palantir/a"},
            {"company_name": "Tesla", "url": "https://www.tesla.com/careers/1"},
        ]
        boards = companies.discover(listings)
        self.assertEqual([(b["ats"], b["company"]) for b in boards], [("greenhouse", "Stripe"), ("lever", "Palantir")])


class ParserTests(unittest.TestCase):
    def test_greenhouse(self):
        jobs = sources.parse_greenhouse({"ats": "greenhouse", "slug": "acme", "company": "Acme"}, GREENHOUSE)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["id"], "greenhouse:acme:101")
        self.assertEqual(jobs[0]["location"], "New York, NY")
        self.assertEqual(jobs[0]["company"], "Acme")

    def test_lever(self):
        [job] = sources.parse_lever({"ats": "lever", "slug": "beta", "company": "Beta"}, LEVER)
        self.assertEqual(job["title"], "New Grad Software Engineer")
        self.assertTrue(job["posted"].startswith(str(NOW.year)))

    def test_ashby_skips_unlisted(self):
        jobs = sources.parse_ashby({"ats": "ashby", "slug": "gamma", "company": "Gamma"}, ASHBY)
        self.assertEqual([j["title"] for j in jobs], ["Security Engineering Intern"])

    def test_smartrecruiters(self):
        [job] = sources.parse_smartrecruiters({"ats": "smartrecruiters", "slug": "Delta", "company": "Delta"}, SMARTRECRUITERS)
        self.assertEqual(job["location"], "Raleigh, NC, US")
        self.assertEqual(job["url"], "https://jobs.smartrecruiters.com/Delta/744")

    def test_workday(self):
        board = {"ats": "workday", "host": "eps.wd1.myworkdayjobs.com", "tenant": "eps", "site": "Careers", "company": "Eps"}
        [job] = sources.parse_workday(board, WORKDAY)
        self.assertEqual(job["url"], "https://eps.wd1.myworkdayjobs.com/Careers/job/Raleigh-NC/Associate-Software-Engineer_R123")


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.f = JobFilter(CONFIG)

    def test_matches(self):
        cases = {
            "Software Engineer Intern, Summer 2027": "internship",
            "Cybersecurity Co-op": "internship",
            "Student Software Developer": "internship",
            "New Grad Software Engineer": "full_time",
            "Software Engineer I": "full_time",
            "Associate Software Engineer": "full_time",
            "Entry-Level Security Analyst": "full_time",
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(self.f.role_type({"title": title}), expected)

    def test_rejects(self):
        for title in ["Senior Software Engineer", "Software Engineer II", "Staff Engineer, New Grad Hiring",
                      "Marketing Intern", "Engineering Manager", "PhD Research Intern, AI", "Account Executive"]:
            with self.subTest(title=title):
                self.assertIsNone(self.f.role_type({"title": title}))

    def test_location_filter(self):
        f = JobFilter({**CONFIG, "locations": ["NC", "Remote"]})
        self.assertEqual(f.role_type({"title": "SWE Intern", "location": "Raleigh, NC"}), "internship")
        self.assertIsNone(f.role_type({"title": "SWE Intern", "location": "London, UK"}))


def fake_results():
    return {
        "greenhouse:us:acme": sources.parse_greenhouse({"ats": "greenhouse", "slug": "acme", "company": "Acme"}, GREENHOUSE),
        "lever::beta": sources.parse_lever({"ats": "lever", "slug": "beta", "company": "Beta"}, LEVER),
    }


class SelectNewTests(unittest.TestCase):
    def setUp(self):
        self.f = JobFilter(CONFIG)

    def test_first_run_notifies_nothing(self):
        st = state_mod.load("/nonexistent")
        new, matched = main.select_new(fake_results(), st, self.f, 7)
        self.assertEqual(new, [])
        self.assertEqual(len(matched), 2)  # intern + new grad; the senior role is filtered out

    def test_only_unseen_jobs_on_known_boards(self):
        st = state_mod.load("/nonexistent")
        main.select_new(fake_results(), st, self.f, 7)
        st["seeded"] = True
        state_mod.mark_seen(st, ["greenhouse:acme:101"])
        new, _ = main.select_new(fake_results(), st, self.f, 7)
        self.assertEqual([j["id"] for j in new], ["lever:beta:abc-1"])
        self.assertEqual(new[0]["role_type"], "full_time")

    def test_new_board_is_silent(self):
        st = {"seeded": True, "known_boards": ["greenhouse:us:acme"], "boards": {}, "seen": {}}
        new, _ = main.select_new(fake_results(), st, self.f, 7)
        self.assertEqual([j["company"] for j in new], ["Acme"])  # Beta's board is new, so it's recorded silently
        self.assertIn("lever::beta", st["known_boards"])

    def test_old_postings_skipped(self):
        self.assertFalse(main.is_recent({"posted": OLD}, 7))
        self.assertTrue(main.is_recent({"posted": RECENT}, 7))
        self.assertTrue(main.is_recent({"posted": None}, 7))
        z_format = (NOW - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertTrue(main.is_recent({"posted": z_format}, 7))


class StateTests(unittest.TestCase):
    def test_save_load_prune(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state", "state.json")
            st = state_mod.load(path)
            state_mod.mark_seen(st, ["old"], when=(NOW - timedelta(days=20)).isoformat())
            state_mod.mark_seen(st, ["fresh"])
            self.assertEqual(state_mod.prune(st), 1)
            state_mod.save(path, st)
            self.assertEqual(set(state_mod.load(path)["seen"]), {"fresh"})


class NotifyTests(unittest.TestCase):
    def job(self, i):
        return {"company": f"Co{i}", "title": f"SWE Intern {i}", "location": "Remote",
                "url": f"https://x/{i}", "role_type": "internship"}

    def test_individual_then_summary(self):
        with mock.patch.object(notify, "_publish") as pub:
            self.assertEqual(notify.notify("t", [self.job(i) for i in range(3)]), 3)
            self.assertEqual(pub.call_args[0][1]["click"], "https://x/2")
            self.assertEqual(pub.call_args[0][1]["title"], "Internship · Co2")
        with mock.patch.object(notify, "_publish") as pub:
            self.assertEqual(notify.notify("t", [self.job(i) for i in range(25)], "https://run"), 1)
            msg = pub.call_args[0][1]
            self.assertTrue(msg["title"].startswith("25 new jobs"))
            self.assertIn("…and 17 more", msg["message"])
            self.assertEqual(msg["click"], "https://run")


class EndToEndTests(unittest.TestCase):
    """Runs main() twice with fake network calls: seed, then detect one new job."""

    def test_two_runs(self):
        boards = [{"ats": "greenhouse", "company": "Acme", "slug": "acme", "region": "us"},
                  {"ats": "lever", "company": "Beta", "slug": "beta"}]
        responses = {"greenhouse": GREENHOUSE, "lever": []}
        fetchers = {
            "greenhouse": lambda b: sources.parse_greenhouse(b, responses["greenhouse"]),
            "lever": lambda b: sources.parse_lever(b, responses["lever"]),
        }
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"NTFY_TOPIC": "test-topic"}), \
                mock.patch.object(companies, "load_listings", return_value=[]), \
                mock.patch.object(companies, "discover", return_value=boards), \
                mock.patch.dict(sources.FETCHERS, fetchers, clear=True), \
                mock.patch.object(notify, "_publish") as pub:
            path = os.path.join(d, "state.json")
            args = ["--config", os.path.join(ROOT, "config.json"), "--state", path]

            self.assertEqual(main.main(args), 0)
            self.assertEqual(pub.call_count, 1)
            self.assertEqual(pub.call_args[0][1]["title"], "Job scanner is live")

            responses["lever"] = LEVER  # a new-grad role gets posted
            pub.reset_mock()
            self.assertEqual(main.main(args), 0)
            self.assertEqual(pub.call_count, 1)
            self.assertEqual(pub.call_args[0][1]["title"], "Full-time · Beta")

            pub.reset_mock()
            self.assertEqual(main.main(args), 0)  # nothing new: no notification
            pub.assert_not_called()


class WorkdayDateTests(unittest.TestCase):
    def test_old_workday_jobs_are_skipped(self):
        self.assertFalse(main.is_recent({"posted": sources.workday_posted("Posted 30+ Days Ago")}, 7))
        self.assertTrue(main.is_recent({"posted": sources.workday_posted("Posted Today")}, 7))
        self.assertTrue(main.is_recent({"posted": sources.workday_posted("Posted 3 Days Ago")}, 7))
        self.assertIsNone(sources.workday_posted(None))


if __name__ == "__main__":
    unittest.main()
