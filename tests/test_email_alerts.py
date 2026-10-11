"""
Job-alert email parsing and the read-only inbox reader. The sample emails are hand-written in the shapes
these sites use; real alert layouts change, so a first real run should be checked against the inbox.

Run: python -m unittest discover -s tests
"""

from email.message import EmailMessage
import unittest
from unittest import mock

from src.opportunities import email_alerts as ea

LINKEDIN_HTML = """
<html><head><style>.x{color:red}</style></head><body><table>
<tr><td><a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc"><img src="logo.png"></a></td>
<td><a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc">Software Engineer Intern</a>
<p>Razorpay · Bengaluru, Karnataka, India</p><p>Actively recruiting</p><p>3 connections</p></td></tr>
<tr><td><a href="https://www.linkedin.com/comm/jobs/view/4099999999/?refId=1">Backend Developer Intern (Python)</a>
<p>Applied Materials</p><p>Remote</p><p>Easy Apply</p></td></tr>
<tr><td><a href="https://www.linkedin.com/comm/jobs/alerts">Manage job alerts</a></td></tr>
</table></body></html>
"""

LINKEDIN_TEXT = """Your job alert for software engineer intern

Software Engineer Intern
Razorpay
Bengaluru, Karnataka, India
View job: https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc

Full Stack Intern
Zepto
Mumbai, Maharashtra, India
View job: https://www.linkedin.com/comm/jobs/view/4011111111/

Unsubscribe: https://www.linkedin.com/comm/psettings/email-unsubscribe
"""

INTERNSHALA_HTML = """
<div><a href="https://internshala.com/internship/detail/web-development-internship-in-ahmedabad-at-acme1234">Web Development</a>
<div>Acme Labs</div><div>Ahmedabad</div><div>Stipend: 10,000 /month</div></div>
<div><a href="https://internshala.com/internship/detail/web-development-internship-in-ahmedabad-at-acme1234">Apply now</a></div>
"""


def message(sender, subject, html=None, text=None):
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = subject
    msg["Date"] = "Sat, 10 Oct 2026 09:00:00 +0530"
    if text:
        msg.set_content(text)
    if html:
        if text:
            msg.add_alternative(html, subtype="html")
        else:
            msg.set_content(html, subtype="html")
    return msg


class ParseTest(unittest.TestCase):
    def test_linkedin_html_cards(self):
        jobs = ea.jobs_from_email("linkedin", html=LINKEDIN_HTML)
        self.assertEqual([j["id"] for j in jobs], ["4012345678", "4099999999"])
        self.assertEqual(jobs[0], {"id": "4012345678", "title": "Software Engineer Intern", "company": "Razorpay",
                                   "location": "Bengaluru, Karnataka, India"})
        self.assertEqual(jobs[1]["company"], "Applied Materials")  # not mistaken for an "apply" badge
        self.assertEqual(jobs[1]["location"], "Remote")

    def test_linkedin_plain_text(self):
        jobs = ea.jobs_from_email("linkedin", text=LINKEDIN_TEXT)
        self.assertEqual([(j["title"], j["company"], j["location"]) for j in jobs], [
            ("Software Engineer Intern", "Razorpay", "Bengaluru, Karnataka, India"),
            ("Full Stack Intern", "Zepto", "Mumbai, Maharashtra, India"),
        ])

    def test_internshala_html_dedupes_apply_link(self):
        jobs = ea.jobs_from_email("internshala", html=INTERNSHALA_HTML)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Web Development")
        self.assertEqual(jobs[0]["company"], "Acme Labs")

    def test_items_from_message_builds_clean_linkedin_items(self):
        items = ea.items_from_message(message("LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
                                              "Software Engineer Intern: Razorpay and more", html=LINKEDIN_HTML,
                                              text=LINKEDIN_TEXT))
        first = items[0]
        self.assertEqual(first["source"], "alerts")
        self.assertEqual(first["external_id"], "linkedin:4012345678")
        self.assertEqual(first["url"], "https://www.linkedin.com/jobs/view/4012345678/")  # tracking stripped
        self.assertEqual(first["kind"], "internship")
        self.assertEqual(first["posted_at"], "2026-10-10T03:30:00+00:00")

    def test_other_senders_are_ignored(self):
        self.assertEqual(ea.items_from_message(message("Someone <a@b.com>", "jobs", html=LINKEDIN_HTML)), [])


class FakeImap:
    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    def login(self, user, password):
        self.calls.append(("login", user))

    def select(self, box, readonly=False):
        self.calls.append(("select", box, readonly))
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        sender = criteria[-1].strip('"')
        hits = [str(n).encode() for n, m in enumerate(self.messages, 1) if sender in m["From"]]
        return "OK", [b" ".join(hits)]

    def fetch(self, msg_id, spec):
        self.calls.append(("fetch", spec))
        return "OK", [(b"1", self.messages[int(msg_id) - 1].as_bytes())]

    def logout(self):
        self.calls.append(("logout",))


class InboxTest(unittest.TestCase):
    env = {"SMTP_USER": "me@gmail.com", "SMTP_PASSWORD": "abcd efgh ijkl mnop"}

    def test_reads_read_only_and_dedupes_across_emails(self):
        imap = FakeImap([
            message("jobalerts-noreply@linkedin.com", "alert 1", html=LINKEDIN_HTML),
            message("jobalerts-noreply@linkedin.com", "alert 2", text=LINKEDIN_TEXT),
            message("Internshala <student@internshala.com>", "New internships", html=INTERNSHALA_HTML),
        ])
        with mock.patch.dict("os.environ", self.env, clear=False), \
             mock.patch.object(ea.imaplib, "IMAP4_SSL", return_value=imap) as ssl:
            items = ea.fetch_email_alerts()
        ssl.assert_called_once_with("imap.gmail.com", timeout=30)
        self.assertIn(("select", "INBOX", True), imap.calls)
        self.assertTrue(all(c[1] == "(BODY.PEEK[])" for c in imap.calls if c[0] == "fetch"))
        self.assertEqual(sorted(i["external_id"] for i in items),
                         ["internshala:internship/detail/web-development-internship-in-ahmedabad-at-acme1234",
                          "linkedin:4011111111", "linkedin:4012345678", "linkedin:4099999999"])
        self.assertEqual(imap.calls[-1], ("logout",))

    def test_not_configured_says_what_to_do(self):
        with mock.patch.dict("os.environ", {"SMTP_USER": "", "SMTP_PASSWORD": "", "IMAP_USER": "", "IMAP_PASSWORD": ""}):
            with self.assertRaisesRegex(RuntimeError, "SMTP_USER and SMTP_PASSWORD"):
                ea.fetch_email_alerts()

    def test_refused_login_explains_app_password(self):
        imap = mock.Mock()
        imap.login.side_effect = ea.imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        with mock.patch.dict("os.environ", self.env), mock.patch.object(ea.imaplib, "IMAP4_SSL", return_value=imap):
            with self.assertRaisesRegex(RuntimeError, "app password"):
                ea.fetch_email_alerts()


if __name__ == "__main__":
    unittest.main()


from tests.test_tracking import TempDataCase  # noqa: E402  (shared throwaway-database setup)


class ScanTest(TempDataCase):
    def test_scan_saves_alert_jobs_labelled_by_site(self):
        from src.opportunities import refresh
        msg = message("jobalerts-noreply@linkedin.com", "alert", text=LINKEDIN_TEXT)
        with mock.patch.dict(refresh.SOURCES["alerts"], {"fetch": lambda q: ea.items_from_message(msg)}):
            with mock.patch("src.opportunities.companies.crawl_companies",
                            return_value={"checked": 0, "new": 0}):  # no network in tests
                report = refresh.refresh_sources(manual=True, only=["alerts"])
        self.assertEqual(report["sources"]["alerts"]["new"], 2)
        from src.core.db import get_db_connection
        conn = get_db_connection()
        opp_id = conn.execute("SELECT id FROM opportunities WHERE external_id = 'linkedin:4012345678'").fetchone()[0]
        conn.close()
        item = self.client.get(f"/api/items/opp:{opp_id}").json()
        self.assertEqual(item["source_label"], "LinkedIn")
        self.assertEqual(item["company"], "Razorpay")


class PlainErrorTest(unittest.TestCase):
    def test_network_errors_read_as_one_sentence(self):
        import requests
        from src.opportunities.sources import plain_error
        dns = requests.exceptions.ConnectionError("HTTPSConnectionPool(host='x'): Max retries (NameResolutionError: Failed to resolve 'x')")
        self.assertTrue(plain_error(dns).startswith("couldn't connect: no internet"))
        self.assertIn("too long", plain_error(requests.exceptions.ReadTimeout("Read timed out")))
        resp = requests.Response()
        resp.status_code = 503
        self.assertIn("HTTP 503", plain_error(requests.exceptions.HTTPError(response=resp)))
        self.assertEqual(plain_error(ValueError("bad data")), "bad data")
