"""
Job-alert emails from your own inbox: LinkedIn, Internshala and Naukri email you matching jobs, and SARTHI
reads those emails over IMAP. It never opens, logs in to or fetches anything from those sites; it only
reads mail they already sent you, read-only (messages are not even marked as read).

Uses the Gmail app password from .env (SMTP_USER / SMTP_PASSWORD, or IMAP_USER / IMAP_PASSWORD).
"""

from datetime import datetime, timedelta, timezone
import email
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
import imaplib
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from src.opportunities.normalize import make_item

logger = logging.getLogger("sarthi.email_alerts")

LOOKBACK_DAYS = 14
MAX_EMAILS = 60

# Who sends the alerts, and what a link to one job looks like (group 1 = the job's id on that site).
SITES: Dict[str, Dict[str, Any]] = {
    "linkedin": {
        "label": "LinkedIn",
        "senders": ["jobalerts-noreply@linkedin.com", "jobs-noreply@linkedin.com", "jobs-listings@linkedin.com"],
        "job_url": re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:comm/)?jobs/view/(?:[\w-]*?-)?(\d{6,})", re.I),
        "canonical": "https://www.linkedin.com/jobs/view/{id}/",
    },
    "internshala": {
        "label": "Internshala",
        "senders": ["internshala.com"],
        "job_url": re.compile(r"https?://(?:www\.)?internshala\.com/((?:internship|job)/detail/[\w-]+)", re.I),
        "canonical": "https://internshala.com/{id}",
    },
    "naukri": {
        "label": "Naukri",
        "senders": ["naukri.com"],
        "job_url": re.compile(r"https?://(?:www\.)?naukri\.com/(job-listings-[\w-]+)", re.I),
        "canonical": "https://www.naukri.com/{id}",
    },
}

GENERIC_LINK_TEXT = re.compile(r"^(view( job| details)?|apply( now)?|see (job|more)|easy apply|save|details|more|\W*)$", re.I)
# Badges and footer lines that sit between job cards; never a company or a location.
NOISE_LINE = re.compile(
    r"\beasy apply\b|^apply\b|actively recruiting|\bconnections?\b|\bapplicants?\b|\balumni\b|^promoted$|\bviewed\b|"
    r"\b\d+ (?:minutes?|hours?|days?|weeks?|months?) ago\b|^new$|see all jobs|unsubscribe|manage (?:job )?alerts|"
    r"\bjob alert\b|you(?:'re| are) receiving|linkedin corporation|©|^view job$", re.I)


def imap_config() -> Dict[str, str]:
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    return {
        "host": os.getenv("IMAP_HOST") or ("imap.gmail.com" if "gmail" in smtp_host else smtp_host.replace("smtp.", "imap.", 1)),
        "user": (os.getenv("IMAP_USER") or os.getenv("SMTP_USER") or "").strip(),
        "password": (os.getenv("IMAP_PASSWORD") or os.getenv("SMTP_PASSWORD") or "").replace(" ", ""),
    }


def is_configured() -> bool:
    cfg = imap_config()
    return bool(cfg["user"] and cfg["password"])


# ---------------------------------------------------------------- parsing one email

class _Segments(HTMLParser):
    """Flatten email HTML into ordered (text, href) segments: link text carries its href, other text has None."""

    BLOCK = {"br", "p", "div", "tr", "td", "li", "table", "h1", "h2", "h3", "h4", "span"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: List[Tuple[str, Optional[str]]] = []
        self.buf: List[str] = []
        self.href: Optional[str] = None
        self.skip = 0

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        if text or self.href:
            self.out.append((text, self.href))
        self.buf = []

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head"):
            self.skip += 1
        elif tag == "a":
            self._flush()
            self.href = dict(attrs).get("href")
        elif tag in self.BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head"):
            self.skip = max(0, self.skip - 1)
        elif tag == "a":
            self._flush()
            self.href = None
        elif tag in self.BLOCK:
            self._flush()

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)

    def close(self):
        super().close()
        self._flush()


def _segments_from_text(text: str) -> List[Tuple[str, Optional[str]]]:
    """Plain-text alerts: each line is a segment; a bare URL (or 'View job: URL') becomes a link segment."""
    out: List[Tuple[str, Optional[str]]] = []
    for line in text.splitlines():
        line = line.strip()
        url = re.search(r"https?://\S+", line)
        if url:
            out.append(("", url.group(0).rstrip(">).,")))
        elif line:
            out.append((line, None))
    return out


def _clean(line: str) -> str:
    return re.sub(r"\s+", " ", unescape(line)).strip(" ·|-")


def jobs_from_email(site: str, html: str = "", text: str = "") -> List[Dict[str, str]]:
    """
    Job cards in one alert email: [{"id", "title", "company", "location"}].
    HTML: the job link's own text is the title, and the next lines are company and location.
    Plain text: the lines just before the job link are title, company and location.
    """
    pattern = SITES[site]["job_url"]
    if html:
        parser = _Segments()
        parser.feed(html)
        parser.close()
        segs = parser.out
    else:
        segs = _segments_from_text(text)

    jobs: Dict[str, Dict[str, str]] = {}
    order: List[str] = []
    positions = [(i, pattern.search(href or "")) for i, (_, href) in enumerate(segs)]
    job_at = [(i, m.group(1)) for i, m in positions if m]
    for n, (i, job_id) in enumerate(job_at):
        job = jobs.setdefault(job_id, {"id": job_id, "title": "", "company": "", "location": ""})
        if job_id not in order:
            order.append(job_id)
        anchor = _clean(segs[i][0])
        if html and anchor and not GENERIC_LINK_TEXT.match(anchor) and len(anchor) > len(job["title"]):
            job["title"] = anchor
        if job["company"]:
            continue
        if html:
            stop = next((j for j, _ in job_at[n + 1:] if j > i), len(segs))
            context = [_clean(t) for t, h in segs[i + 1:stop] if t and not (h and pattern.search(h))]
        else:
            start = job_at[n - 1][0] + 1 if n else 0
            context = [_clean(t) for t, h in segs[start:i] if t and not h]
        context = [c for c in context if c and not NOISE_LINE.search(c) and len(c) < 120]
        if not html:
            context = context[-3:]
            if context and not job["title"]:
                job["title"] = context.pop(0)
        elif not job["title"] and context:
            job["title"] = context.pop(0)
        if context and " · " in context[0]:
            job["company"], _, job["location"] = context[0].partition(" · ")
        else:
            job["company"] = context[0] if context else ""
            job["location"] = context[1] if len(context) > 1 else ""
    return [jobs[j] for j in order if jobs[j]["title"]]


def _decode(value: Optional[str]) -> str:
    return str(make_header(decode_header(value))) if value else ""


def _bodies(msg: email.message.Message) -> Tuple[str, str]:
    html, text = "", ""
    for part in msg.walk():
        kind = part.get_content_type()
        if kind not in ("text/html", "text/plain") or part.get_filename():
            continue
        payload = part.get_payload(decode=True) or b""
        body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if kind == "text/html" and not html:
            html = body
        elif kind == "text/plain" and not text:
            text = body
    return html, text


def site_for(sender: str) -> Optional[str]:
    sender = sender.lower()
    for key, site in SITES.items():
        if any(s in sender for s in site["senders"]):
            return key
    return None


def items_from_message(msg: email.message.Message) -> List[Dict[str, Any]]:
    """Normalized opportunity items for one alert email (empty if it isn't from a known alert sender)."""
    site = site_for(_decode(msg.get("From")))
    if not site:
        return []
    html, text = _bodies(msg)
    subject = _decode(msg.get("Subject"))
    try:
        received = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        received = None
    label = SITES[site]["label"]
    items = []
    for job in jobs_from_email(site, html=html, text=text) or (jobs_from_email(site, text=text) if html and text else []):
        items.append(make_item(
            source="alerts",
            source_label=f"{label} alert",
            external_id=f"{site}:{job['id']}",
            title=job["title"],
            company=job["company"],
            url=SITES[site]["canonical"].format(id=job["id"]),
            description=f"From your {label} job alert email: \"{subject}\". Open the posting on {label} for the full description.",
            locations=[job["location"]] if job["location"] else None,
            posted_at=received,
        ))
    return items


# ---------------------------------------------------------------- reading the inbox

def fetch_email_alerts(query: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read recent alert emails (read-only) and return one item per job they list."""
    if not is_configured():
        raise RuntimeError("not connected. Add SMTP_USER and SMTP_PASSWORD (a Gmail app password) to .env, then restart.")
    cfg = imap_config()
    since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%d-%b-%Y")
    try:
        conn = imaplib.IMAP4_SSL(cfg["host"], timeout=30)
        conn.login(cfg["user"], cfg["password"])
    except imaplib.IMAP4.error as e:
        raise RuntimeError(f"your email login was refused ({e}). Use a Gmail app password, not your normal password.")
    try:
        conn.select("INBOX", readonly=True)
        ids: List[bytes] = []
        for site in SITES.values():
            for sender in site["senders"]:
                status, data = conn.search(None, "SINCE", since, "FROM", f'"{sender}"')
                if status == "OK" and data and data[0]:
                    ids += [i for i in data[0].split() if i not in ids]
        ids = sorted(ids, key=int)[-MAX_EMAILS:]
        found: Dict[str, Dict[str, Any]] = {}
        for msg_id in ids:
            status, data = conn.fetch(msg_id, "(BODY.PEEK[])")  # PEEK: leaves the email unread
            if status != "OK" or not data or not isinstance(data[0], tuple):
                continue
            for item in items_from_message(email.message_from_bytes(data[0][1])):
                found[item["external_id"]] = item  # later emails win, so a job keeps its newest details
        logger.info(f"email alerts: {len(ids)} alert emails, {len(found)} jobs")
        return list(found.values())
    finally:
        try:
            conn.logout()
        except Exception:
            pass
