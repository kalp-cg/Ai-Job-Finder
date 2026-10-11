"""
Follow-up reminders, notes, send log, queue diagnostics and alerts over leads.
Every test runs against a throwaway database and state file, never data/.

Run: python -m unittest discover -s tests
"""

import io
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import src.core.db as db
import src.opportunities.state as state_mod
from src.opportunities import refresh
from src.opportunities.normalize import dedupe_key
from src.opportunities.ranking import Query, rank
from src.web import app as web


def posting(ext_id, title, **extra):
    base = {
        "source": "muse", "external_id": ext_id, "kind": "internship", "title": title, "company": f"Co {ext_id}",
        "url": f"https://example.com/{ext_id}", "description": "Python and React work.", "location": "Remote",
        "remote": True, "skills": ["Python", "React"], "pay_text": "", "pay_monthly_usd": None,
        "experience_years": None, "contact_email": None, "posted_at": datetime.now(timezone.utc).isoformat(),
        "deadline": None,
    }
    return {**base, **extra}


class TempDataCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(db, "DB_PATH", tmp / "test.db"),
            mock.patch.object(state_mod, "STATE_PATH", tmp / "state.json"),
        ]
        for p in self.patches:
            p.start()
        db.init_db()
        self.client = TestClient(web.app)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def add_opp(self, ext_id="1", title="Software Engineering Intern", **extra):
        db.upsert_opportunities([posting(ext_id, title, **extra)], dedupe_key)
        conn = db.get_db_connection()
        opp_id = conn.execute("SELECT id FROM opportunities WHERE external_id = ?", (ext_id,)).fetchone()[0]
        conn.close()
        return opp_id

    def add_lead(self, title="Need a Python developer for a freelance scraper", score=8):
        return db.save_lead(source="reddit", title=title, url=f"https://reddit.com/r/forhire/{title[:10]}",
                            raw_content=f"{title}. Remote, paid $500. Python, FastAPI.", tech=["Python", "FastAPI"],
                            budget="$500", score=score, score_reason="Fits your Python work")


class MigrationTest(unittest.TestCase):
    def test_adds_follow_up_column_and_backfills_waiting_items(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "old.db"
            db.init_db(path)
            conn = sqlite3.connect(path)
            conn.execute("INSERT INTO leads (id, source, url, title, status, updated_at) VALUES "
                         "(1, 'reddit', 'u1', 't', 'contacted', '2026-10-01T10:00:00+00:00'), "
                         "(2, 'reddit', 'u2', 't', 'new', '2026-10-01T10:00:00+00:00')")
            conn.execute("INSERT INTO opportunities (id, source, external_id, title, status, updated_at) "
                         "VALUES (1, 'muse', 'x', 't', 'applied', '2026-10-02 08:00:00')")
            # Back to the schema from before follow-ups existed.
            conn.execute("ALTER TABLE leads DROP COLUMN follow_up_at")
            conn.execute("ALTER TABLE opportunities DROP COLUMN follow_up_at")
            conn.commit()
            conn.close()

            db.init_db(path)
            db.init_db(path)  # second run must be a no-op

            conn = sqlite3.connect(path)
            leads = dict(conn.execute("SELECT id, follow_up_at FROM leads").fetchall())
            opp = conn.execute("SELECT follow_up_at FROM opportunities").fetchone()[0]
            conn.close()
            self.assertEqual(leads[1], "2026-10-06T10:00:00+00:00")
            self.assertIsNone(leads[2])
            self.assertEqual(opp, "2026-10-07T08:00:00+00:00")


class FollowUpTest(TempDataCase):
    def test_waiting_status_sets_reminder_and_later_status_clears_it(self):
        ref = f"opp:{self.add_opp()}"
        self.assertEqual(self.client.post(f"/api/items/{ref}/status", json={"status": "applied"}).status_code, 200)
        due = datetime.fromisoformat(self.client.get(f"/api/items/{ref}").json()["follow_up_at"])
        self.assertAlmostEqual((due - datetime.now(timezone.utc)).total_seconds(), 5 * 86400, delta=60)

        self.client.post(f"/api/items/{ref}/status", json={"status": "interviewing"})
        self.assertIsNone(self.client.get(f"/api/items/{ref}").json()["follow_up_at"])

    def test_lead_contacted_gets_reminder(self):
        ref = f"lead:{self.add_lead()}"
        self.client.post(f"/api/items/{ref}/status", json={"status": "contacted"})
        self.assertIsNotNone(self.client.get(f"/api/items/{ref}").json()["follow_up_at"])

    def test_set_and_clear_follow_up_date(self):
        ref = f"lead:{self.add_lead()}"
        res = self.client.put(f"/api/items/{ref}/follow-up", json={"follow_up_at": "2026-10-20T03:30:00.000Z"})
        self.assertEqual(res.json()["follow_up_at"], "2026-10-20T03:30:00+00:00")
        self.assertEqual(self.client.get(f"/api/items/{ref}").json()["follow_up_at"], "2026-10-20T03:30:00+00:00")

        self.client.put(f"/api/items/{ref}/follow-up", json={"follow_up_at": None})
        self.assertIsNone(self.client.get(f"/api/items/{ref}").json()["follow_up_at"])

        self.assertEqual(self.client.put(f"/api/items/{ref}/follow-up", json={"follow_up_at": "next week"}).status_code, 400)
        self.assertEqual(self.client.put("/api/items/opp:999/follow-up", json={"follow_up_at": None}).status_code, 404)

    def test_send_moves_to_sent_with_reminder_and_logs_it(self):
        opp_id = self.add_opp(contact_email="jobs@example.com")
        ref = f"opp:{opp_id}"

        def fake_send(item_ref, to, subject, body, attach_resume, daily_cap):
            db.record_outreach(item_ref, to, subject, body, None, ok=True)
            return {"ok": True, "attached": None}

        with mock.patch.object(web.mailer, "send_email", side_effect=fake_send):
            res = self.client.post("/api/outreach/send", json={"ref": ref, "to": "jobs@example.com",
                                                               "subject": "Application", "body": "Hi"})
        self.assertTrue(res.json()["ok"])
        item = self.client.get(f"/api/items/{ref}").json()
        self.assertEqual(item["status"], "applied")
        self.assertIsNotNone(item["follow_up_at"])
        self.assertEqual([o["to_email"] for o in item["outreach"]], ["jobs@example.com"])

        listed = next(i for i in self.client.get("/api/items").json()["items"] if i["ref"] == ref)
        self.assertEqual(len(listed["outreach"]), 1)


class NotesTest(TempDataCase):
    def test_notes_save_for_leads_and_opportunities(self):
        for ref in (f"lead:{self.add_lead()}", f"opp:{self.add_opp()}"):
            res = self.client.put(f"/api/items/{ref}/notes", json={"notes": "  Spoke to Priya, interview Tue  "})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(self.client.get(f"/api/items/{ref}").json()["notes"], "Spoke to Priya, interview Tue")
        self.assertEqual(self.client.put("/api/items/lead:999/notes", json={"notes": "x"}).status_code, 404)

    def test_notes_do_not_change_lead_status(self):
        lead_id = self.add_lead()
        self.client.put(f"/api/items/lead:{lead_id}/notes", json={"notes": "x"})
        self.assertEqual(db.get_lead(lead_id)["status"], "new")


class QueueStatsTest(TempDataCase):
    def test_rank_counts_every_dropped_item(self):
        now = datetime.now(timezone.utc)
        items = [
            {**posting("a", "Software Engineering Intern"), "id": 1},
            {**posting("b", "Software Engineering Intern"), "id": 2, "kind": "job"},
            {**posting("c", "Software Engineering Intern"), "id": 3, "deadline": (now - timedelta(days=1)).isoformat()},
            {**posting("d", "Software Engineering Intern"), "id": 4, "remote": False, "location": "Berlin"},
            {**posting("e", "Sales Intern", description="Cold calls", skills=[]), "id": 5},
            {**posting("f", "Backend Developer Intern"), "id": 6},
            {**posting("g", "Python Developer Intern"), "id": 7},
            {**posting("h", "Full Stack Developer Intern"), "id": 8},
        ]
        stats = {}
        q = Query(kinds=["internship"], skills=["Python"], locations=["india", "remote"])
        results = rank(items, q, limit=3, min_score=5.0, stats=stats)
        self.assertEqual(len(results), 3)
        self.assertEqual(stats.get("kind"), 1)
        self.assertEqual(stats.get("closed"), 1)
        self.assertEqual(stats.get("location"), 1)
        self.assertEqual(stats.get("low_score"), 1)
        self.assertEqual(stats.get("over_limit"), 1)
        self.assertEqual(len(results) + sum(stats.values()), len(items))

    def test_items_endpoint_accounts_for_every_open_posting(self):
        for n in range(5):
            self.add_opp(str(n), f"Python Developer Intern {n}")
        self.add_opp("u", "Software Intern", source="unstop")  # unstop is off by default
        stats = self.client.get("/api/items").json()["queue_stats"]
        self.assertEqual(stats["open"], 6)
        self.assertEqual(stats["disabled_sources"], 1)
        counted = {k: v for k, v in stats.items() if k not in ("open", "shown", "limit", "min_score")}
        self.assertEqual(stats["shown"] + sum(counted.values()), stats["open"])


class WatchingTest(TempDataCase):
    def test_items_say_whether_their_company_is_watched(self):
        watched = self.add_opp("w", "Backend Intern", company="Acme Technologies")
        other = self.add_opp("o", "Backend Intern", company="Globex")
        db.add_company("Acme", ats="greenhouse", slug="acme")
        db.add_company("Globex", origin="user")  # discovery not finished, so not watched yet

        self.assertTrue(self.client.get(f"/api/items/opp:{watched}").json()["watching"])
        self.assertFalse(self.client.get(f"/api/items/opp:{other}").json()["watching"])
        listed = {i["ref"]: i["watching"] for i in self.client.get("/api/items").json()["items"]}
        self.assertTrue(listed[f"opp:{watched}"])


    def test_track_command_works_inside_a_post_thread(self):
        ref = f"opp:{self.add_opp()}"
        with mock.patch.object(web, "track_reply", return_value={"reply": "Watching", "results": []}) as tr, \
             mock.patch.object(web, "handle_lead_message") as lead_chat:
            self.client.post("/api/chat", json={"message": "track acme.com", "item_ref": ref})
            self.client.post("/api/chat", json={"message": "make it shorter", "item_ref": ref})
        tr.assert_called_once_with("Acme", "https://acme.com")
        lead_chat.assert_called_once()


    def test_parse_track_request(self):
        self.assertEqual(web.parse_track_request("track acme.com"), ("Acme", "https://acme.com"))
        self.assertEqual(web.parse_track_request("track www.globex.co.in"), ("Globex", "https://www.globex.co.in"))
        self.assertEqual(web.parse_track_request("watch https://initech.io/careers"), ("Initech", "https://initech.io/careers"))
        self.assertEqual(web.parse_track_request("track Stripe"), ("Stripe", None))
        self.assertEqual(web.parse_track_request("track Node.js Inc"), ("Node.js Inc", None))


class ApplyingToolsTest(TempDataCase):
    def setUp(self):
        super().setUp()
        # Template paths only: never call Gemini from tests.
        self.no_llm = mock.patch("src.web.agent.get_llm_client", return_value=mock.Mock(is_available=False))
        self.no_llm.start()

    def tearDown(self):
        self.no_llm.stop()
        super().tearDown()

    def test_timeline_records_each_status_change(self):
        ref = f"opp:{self.add_opp()}"
        for status in ("applied", "interviewing", "offer"):
            self.client.post(f"/api/items/{ref}/status", json={"status": status})
        self.assertEqual([t["status"] for t in self.client.get(f"/api/items/{ref}").json()["timeline"]],
                         ["applied", "interviewing", "offer"])
        self.client.post(f"/api/items/{ref}/status", json={"status": "bogus"})
        self.assertEqual(len(self.client.get(f"/api/items/{ref}").json()["timeline"]), 3)

    def test_skill_gap_lists_known_skills_missing_from_profile(self):
        ref = f"opp:{self.add_opp(skills=['Python', 'Kubernetes', 'Data Analysis Tools', 'Go'])}"
        with mock.patch.object(web, "load_profile_data", return_value={"skills": ["Python"]}):
            gap = self.client.get(f"/api/items/{ref}").json()["missing_skills"]
        self.assertIn("Kubernetes", gap)
        self.assertNotIn("Python", gap)
        self.assertNotIn("Data Analysis Tools", gap)  # not a known skill, so not claimed as a gap

    def test_skill_gap_uses_all_profile_skills_not_just_the_ranking_top_12(self):
        many = [f"Skill{n}" for n in range(12)] + ["Docker"]
        ref = f"opp:{self.add_opp(skills=['Docker'])}"
        with mock.patch.object(web, "load_profile_data", return_value={"skills": {"primary": many}}):
            self.assertEqual(self.client.get(f"/api/items/{ref}").json()["missing_skills"], [])

    def test_follow_up_draft_mentions_role_and_send_date(self):
        ref = f"opp:{self.add_opp(title='Backend Intern')}"
        db.record_outreach(ref, "jobs@example.com", "Application", "Hi", None, ok=True)
        conn = db.get_db_connection()
        conn.execute("UPDATE outreach SET created_at = '2026-10-02T10:00:00+00:00'")
        conn.commit()
        conn.close()
        text = self.client.post(f"/api/items/{ref}/follow-up-draft").json()["text"]
        self.assertIn("Backend Intern", text)
        self.assertIn("Oct 2", text)

    def test_interview_prep_without_gemini_says_so(self):
        ref = f"opp:{self.add_opp()}"
        text = self.client.post(f"/api/items/{ref}/interview-prep").json()["text"]
        self.assertTrue(text.startswith("Gemini isn't configured"))
        self.assertIn("1. ", text)

    def test_export_has_acted_items_only_and_defuses_formulas(self):
        sent = self.add_opp("s", "=HYPERLINK(\"http://evil\")")
        self.add_opp("n", "Untouched Intern")
        self.client.post(f"/api/items/opp:{sent}/status", json={"status": "applied"})
        self.client.put(f"/api/items/opp:{sent}/notes", json={"notes": "-call them Monday"})
        res = self.client.get("/api/export.csv")
        self.assertEqual(res.headers["content-type"].split(";")[0], "text/csv")
        body = res.text
        self.assertIn("'=HYPERLINK", body)
        self.assertIn("'-call them Monday", body)
        self.assertNotIn("Untouched Intern", body)


class RedditSwitchTest(TempDataCase):
    def run_lead_scan(self):
        import main
        hn = mock.Mock(last_error=None)
        hn.fetch_posts.return_value = []
        reddit = mock.Mock(is_configured=True, last_error=None)
        reddit.fetch_posts.return_value = []
        with mock.patch.object(main, "RedditSource", return_value=reddit), \
             mock.patch.object(main, "HackerNewsSource", return_value=hn):
            main.process_leads(use_sample=False)
        return reddit

    def test_turned_off_reddit_is_not_fetched(self):
        state_mod.update_state(lambda s: s["settings"].__setitem__("disabled_sources", ["reddit"]))
        self.run_lead_scan().fetch_posts.assert_not_called()
        self.assertNotIn("reddit", state_mod.load_state()["runs"])

    def test_reddit_is_fetched_when_on(self):
        state_mod.update_state(lambda s: s["settings"].__setitem__("disabled_sources", []))
        self.run_lead_scan().fetch_posts.assert_called_once()


class ResumeAndProfileTest(TempDataCase):
    def setUp(self):
        super().setUp()
        tmp = Path(self.tmp.name)
        (tmp / "config").mkdir()
        import src.web.agent as agent
        import src.opportunities.mailer as mailer
        self.agent = agent
        self.more = [mock.patch.object(agent, "DATA_DIR", tmp), mock.patch.object(agent, "CONFIG_DIR", tmp / "config"),
                     mock.patch.object(mailer, "DATA_DIR", tmp),
                     mock.patch.object(agent, "get_llm_client", return_value=mock.Mock(is_available=False))]
        for p in self.more:
            p.start()
        self.dir = tmp

    def tearDown(self):
        for p in self.more:
            p.stop()
        super().tearDown()

    def save_profile(self):
        self.agent.onboard_user_profile(name="Kalp", skills_input="Python", github_url="https://github.com/kalp-cg",
                                        resume_or_bio="Short bio.")

    def test_saving_profile_keeps_uploaded_resume_and_extra_fields(self):
        (self.dir / "resume.pdf").write_bytes(b"%PDF-1.4")
        (self.dir / "resume.md").write_text("Full resume with PGRadar and DhanPath AI", encoding="utf-8")
        (self.dir / "config" / "profile.yaml").write_text("portfolio: https://kalppatel.me\n", encoding="utf-8")
        self.save_profile()
        self.assertIn("PGRadar", (self.dir / "resume.md").read_text(encoding="utf-8"))
        self.assertEqual(self.agent.load_profile_data()["portfolio"], "https://kalppatel.me")

    def test_saving_profile_never_invents_case_studies(self):
        self.save_profile()
        portfolio = (self.dir / "portfolio.md").read_text(encoding="utf-8")
        self.assertNotIn("FastAPI architecture", portfolio)
        self.assertIn("No case studies written yet", portfolio)

    def test_bio_only_users_still_get_a_resume_file(self):
        self.save_profile()
        self.assertIn("Short bio.", (self.dir / "resume.md").read_text(encoding="utf-8"))

    def test_pdf_links_are_extracted(self):
        import pypdf
        from pypdf.generic import DictionaryObject, NameObject, TextStringObject, ArrayObject
        writer = pypdf.PdfWriter()
        page = writer.add_blank_page(200, 200)
        def link(uri):
            return writer._add_object(DictionaryObject({
                NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Link"),
                NameObject("/Rect"): ArrayObject([]),
                NameObject("/A"): DictionaryObject({NameObject("/S"): NameObject("/URI"), NameObject("/URI"): TextStringObject(uri)})}))
        page[NameObject("/Annots")] = ArrayObject([link("https://github.com/kalp-cg/PgRadar"), link("mailto:x@y.z"),
                                                    link("https://github.com/kalp-cg/PgRadar")])
        buf = io.BytesIO()
        writer.write(buf)
        self.assertEqual(self.agent.pdf_links(pypdf.PdfReader(io.BytesIO(buf.getvalue()))), ["https://github.com/kalp-cg/PgRadar"])


class AlertsOverLeadsTest(TempDataCase):
    def test_alert_counts_new_matching_lead(self):
        self.add_lead()
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        state_mod.update_state(lambda s: s["alerts"].append(
            {"id": "a1", "text": "freelance python", "created_at": past, "last_checked_at": past, "new_count": 0}))
        refresh.update_alerts()
        alert = state_mod.load_state()["alerts"][0]
        self.assertGreaterEqual(alert["total"], 1)
        self.assertGreaterEqual(alert["new_count"], 1)

    def test_sample_and_low_score_leads_are_left_out(self):
        self.add_lead(title="Low fit Python freelance gig", score=3)
        db.save_lead(source="reddit", title="Python freelance sample", url="https://x/sample_1",
                     raw_content="Python freelance", score=9)
        self.assertEqual(refresh.lead_postings(), [])

    def test_chat_search_returns_leads(self):
        lead_id = self.add_lead()
        refs = [r["ref"] for r in web.search_items("freelance python")["results"]]
        self.assertIn(f"lead:{lead_id}", refs)


if __name__ == "__main__":
    unittest.main()
