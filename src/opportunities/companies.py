"""
Company careers engine: find which hiring system a company uses, then poll its board directly.

Why: the same internship a company announces on LinkedIn is almost always on its own careers board,
and boards hosted on Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee and Workday expose
public JSON feeds designed to be read by other sites. Reading those is legal, precise, and light.

Pipeline per company:
  1. discover  - read the company website (robots.txt respected) for links to a hiring system;
                 otherwise probe each system's public API with likely slugs and verify the name.
  2. poll      - fetch the board, keep only roles that fit (internships; entry-level if you want jobs),
                 store them, and mark roles that disappeared as closed.
  3. schedule  - adaptive revisit: boards that post new matches are checked more often (down to 6h),
                 quiet boards back off (up to 7 days). Requests to any one host are spaced >= 1s apart.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import html as html_lib
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
import yaml

from src.core.db import (
    add_company,
    due_companies,
    get_db_connection,
    update_company,
    upsert_opportunities,
)
from src.opportunities.normalize import dedupe_key, make_item, parse_pay

logger = logging.getLogger("sarthi.companies")

ROOT = Path(__file__).resolve().parent.parent.parent
SEED_FILE = ROOT / "config" / "companies.yaml"
YC_CACHE = ROOT / "data" / "yc_companies.json"
YC_DATASET = "https://yc-oss.github.io/api/companies/all.json"
HEADERS = {"User-Agent": "SARTHI/1.0 (personal job search assistant; low-frequency)"}
TIMEOUT = 20

INDIA_RE = re.compile(r"india|bengaluru|bangalore|pune|mumbai|delhi|gurgaon|gurugram|hyderabad|noida|chennai|kolkata|ahmedabad|jaipur|kochi|chandigarh|indore", re.I)
INTERN_RE = re.compile(r"\bintern(ship)?s?\b|\btrainee\b|\bapprentice|\bco-?op\b|\bsummer analyst\b", re.I)
ENTRY_RE = re.compile(r"\b(junior|jr\.?|associate|graduate|new grad|fresher|entry[- ]level|early career|campus|sde[- ]?(1|i)\b|engineer[- ]?(1|i)\b|developer[- ]?(1|i)\b)", re.I)
SENIOR_RE = re.compile(r"\b(senior|sr\.?|staff|principal|lead|manager|director|head|vp|architect)\b", re.I)


# ---------------------------------------------------------------- polite HTTP

_host_lock = threading.Lock()
_host_next: Dict[str, float] = {}
_robots: Dict[str, Optional[robotparser.RobotFileParser]] = {}
_session = requests.Session()
_session.headers.update(HEADERS)


def _wait_turn(url: str, gap: float = 1.0) -> None:
    """Space requests to the same host at least `gap` seconds apart, across threads."""
    host = urlparse(url).netloc
    with _host_lock:
        now = time.monotonic()
        start = max(now, _host_next.get(host, 0.0))
        _host_next[host] = start + gap
    if start > now:
        time.sleep(start - now)


def _allowed(url: str) -> bool:
    """robots.txt check for HTML pages (public JSON APIs are meant for programmatic use)."""
    parts = urlparse(url)
    base = f"{parts.scheme}://{parts.netloc}"
    if base not in _robots:
        rp = robotparser.RobotFileParser()
        try:
            resp = _session.get(base + "/robots.txt", timeout=10)
            rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
        except Exception:
            rp.parse([])
        _robots[base] = rp
    return _robots[base].can_fetch(HEADERS["User-Agent"], url)


def http_get(url: str, *, params: Optional[Dict[str, Any]] = None, html_page: bool = False) -> Optional[requests.Response]:
    if html_page and not _allowed(url):
        logger.info(f"robots.txt disallows {url}")
        return None
    _wait_turn(url)
    try:
        return _session.get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        logger.debug(f"GET {url} failed: {e}")
        return None


def http_post_json(url: str, body: Dict[str, Any]) -> Optional[requests.Response]:
    _wait_turn(url)
    try:
        return _session.post(url, json=body, timeout=TIMEOUT)
    except requests.RequestException:
        return None


# ---------------------------------------------------------------- board adapters
# Each returns a list of raw roles: {id, title, url, location, description, posted_at, pay_text, remote, experience, kind_hint}
# Descriptions are fetched only for roles that pass the relevance filter (see `detail`).

def _gh_jobs(slug: str) -> Optional[List[Dict[str, Any]]]:
    r = http_get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    if not r or r.status_code != 200:
        return None
    return [{
        "id": j["id"], "title": j.get("title", ""), "url": j.get("absolute_url", ""),
        "location": (j.get("location") or {}).get("name", ""), "posted_at": j.get("first_published") or j.get("updated_at"),
        "detail": f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{j['id']}",
    } for j in r.json().get("jobs", [])]


def _lever_jobs(slug: str) -> Optional[List[Dict[str, Any]]]:
    r = http_get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
    if not r or r.status_code != 200:
        return None
    out = []
    for j in r.json():
        cat = j.get("categories") or {}
        out.append({
            "id": j["id"], "title": j.get("text", ""), "url": j.get("hostedUrl", ""),
            "location": ", ".join(cat.get("allLocations") or [cat.get("location") or ""]),
            "description": (j.get("descriptionPlain") or "") + "\n\n" + (j.get("additionalPlain") or ""),
            "posted_at": (j.get("createdAt") or 0) / 1000 or None,
            "remote": j.get("workplaceType") == "remote", "kind_hint": cat.get("commitment"),
        })
    return out


def _ashby_jobs(slug: str) -> Optional[List[Dict[str, Any]]]:
    r = http_get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", params={"includeCompensation": "true"})
    if not r or r.status_code != 200:
        return None
    out = []
    for j in r.json().get("jobs", []):
        if j.get("isListed") is False:
            continue
        comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        out.append({
            "id": j["id"], "title": j.get("title", ""), "url": j.get("jobUrl", ""), "location": ", ".join(filter(None, locs)),
            "description": j.get("descriptionPlain") or "", "posted_at": j.get("publishedAt"),
            "remote": bool(j.get("isRemote")), "pay_text": comp, "kind_hint": j.get("employmentType"),
        })
    return out


def _workable_jobs(slug: str) -> Optional[List[Dict[str, Any]]]:
    r = http_get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", params={"details": "true"})
    if not r or r.status_code != 200:
        return None
    data = r.json()
    return [{
        "id": j.get("shortcode"), "title": j.get("title", ""), "url": j.get("url") or j.get("shortlink", ""),
        "location": ", ".join(filter(None, [j.get("city"), j.get("country")])), "description": j.get("description") or "",
        "posted_at": j.get("published_on") or j.get("created_at"), "remote": bool(j.get("telecommuting")),
        "kind_hint": j.get("employment_type"), "_company": data.get("name"),
    } for j in data.get("jobs", [])]


def _sr_jobs(slug: str) -> Optional[List[Dict[str, Any]]]:
    out, offset = [], 0
    while offset < 500:
        r = http_get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", params={"limit": 100, "offset": offset})
        if not r or r.status_code != 200:
            return None if offset == 0 else out
        data = r.json()
        for j in data.get("content", []):
            loc = j.get("location") or {}
            out.append({
                "id": j["id"], "title": j.get("name", ""), "url": f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                "location": ", ".join(filter(None, [loc.get("city"), loc.get("country", "").upper()])),
                "posted_at": j.get("releasedDate"), "remote": bool(loc.get("remote")),
                "kind_hint": (j.get("typeOfEmployment") or {}).get("label"), "experience_label": (j.get("experienceLevel") or {}).get("label"),
                "detail": j.get("ref"), "_company": (j.get("company") or {}).get("name"),
            })
        offset += 100
        if offset >= data.get("totalFound", 0):
            break
    return out


def _recruitee_jobs(slug: str) -> Optional[List[Dict[str, Any]]]:
    r = http_get(f"https://{slug}.recruitee.com/api/offers/")
    if not r or r.status_code != 200:
        return None
    return [{
        "id": j["id"], "title": j.get("title", ""), "url": j.get("careers_url", ""),
        "location": ", ".join(filter(None, [j.get("city"), j.get("country")])), "description": j.get("description") or "",
        "posted_at": j.get("published_at"), "remote": bool(j.get("remote")), "kind_hint": j.get("employment_type_code"),
    } for j in r.json().get("offers", [])]


def _workday_jobs(slug: str) -> Optional[List[Dict[str, Any]]]:
    tenant, wd, site = slug.split("|")
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    out = []
    for term in ("intern", "graduate"):
        r = http_post_json(f"{base}/wday/cxs/{tenant}/{site}/jobs", {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": term})
        if not r or r.status_code != 200:
            return None if not out else out
        for j in r.json().get("jobPostings", []):
            out.append({"id": j.get("externalPath"), "title": j.get("title", ""), "url": f"{base}/{site}{j.get('externalPath', '')}",
                        "location": j.get("locationsText", ""), "posted_at": None})
    return out


def _yc_jobs(slug: str) -> Optional[List[Dict[str, Any]]]:
    url = f"https://www.ycombinator.com/companies/{slug}/jobs"
    r = http_get(url, html_page=True)
    if not r or r.status_code != 200:
        return None
    m = re.search(r'data-page="([^"]+)"', r.text)
    if not m:
        return []
    props = json.loads(html_lib.unescape(m.group(1))).get("props", {})
    out = []
    for j in props.get("jobPostings") or []:
        out.append({
            "id": j["id"], "title": j.get("title", ""), "url": "https://www.ycombinator.com" + j.get("url", ""),
            "location": j.get("location") or "", "pay_text": j.get("salaryRange") or "",
            "experience_label": j.get("minExperience"), "kind_hint": j.get("type"),
            "skills": j.get("skills") or [], "remote": "remote" in (j.get("location") or "").lower(),
            "detail": "https://www.ycombinator.com" + j.get("url", ""), "posted_at": None,
        })
    return out


def _jsonld_jobs(careers_url: str) -> Optional[List[Dict[str, Any]]]:
    """Any careers page that publishes schema.org JobPosting data (what Google Jobs reads)."""
    r = http_get(careers_url, html_page=True)
    if not r or r.status_code != 200:
        return None
    return [_from_jsonld(p, careers_url) for p in _jsonld_postings(r.text)]


ROLE_TEXT_RE = re.compile(r"\b(engineer|developer|intern(ship)?|analyst|designer|scientist|associate|trainee|programmer|sde|devops|architect|researcher)\b", re.I)


def _html_jobs(careers_url: str) -> Optional[List[Dict[str, Any]]]:
    """Plain HTML careers pages: role links are anchors whose text reads like a job title."""
    r = http_get(careers_url, html_page=True)
    if not r or r.status_code != 200:
        return None
    postings = _jsonld_postings(r.text)
    if postings:
        return [_from_jsonld(p, r.url) for p in postings]
    out, seen = [], set()
    for href, inner in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', r.text, re.S | re.I):
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_lib.unescape(inner))).strip()
        if not (6 <= len(text) <= 120) or not ROLE_TEXT_RE.search(text) or NOT_ROLE_TEXT.search(text) \
                or re.search(r"apply|learn more|read more|view all", text, re.I):
            continue
        url = urljoin(r.url, html_lib.unescape(href))
        if not ROLE_HREF.search(urlparse(url).path + urlparse(url).netloc):
            continue
        if url in seen or url.rstrip("/") == r.url.rstrip("/"):
            continue
        seen.add(url)
        out.append({"id": url, "title": text, "url": url, "location": "", "posted_at": None, "detail": url})
    return out if len(out) >= 2 else []


ADAPTERS = {
    "greenhouse": _gh_jobs, "lever": _lever_jobs, "ashby": _ashby_jobs, "workable": _workable_jobs,
    "smartrecruiters": _sr_jobs, "recruitee": _recruitee_jobs, "workday": _workday_jobs, "yc": _yc_jobs, "jsonld": _jsonld_jobs, "html": _html_jobs,
}
ATS_LABELS = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "workable": "Workable",
              "smartrecruiters": "SmartRecruiters", "recruitee": "Recruitee", "workday": "Workday", "yc": "YC", "jsonld": "careers page", "html": "careers page"}


def _jsonld_postings(page: str) -> List[Dict[str, Any]]:
    found = []
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if node.get("@type") == "JobPosting" or (isinstance(node.get("@type"), list) and "JobPosting" in node["@type"]):
                    found.append(node)
                stack.extend(v for k, v in node.items() if k in ("@graph", "itemListElement", "item"))
    return found


def _from_jsonld(p: Dict[str, Any], page_url: str) -> Dict[str, Any]:
    locs = p.get("jobLocation") or []
    locs = locs if isinstance(locs, list) else [locs]
    names = []
    for l in locs:
        addr = (l or {}).get("address") or {}
        if isinstance(addr, dict):
            names.append(", ".join(filter(None, [addr.get("addressLocality"), addr.get("addressCountry") if isinstance(addr.get("addressCountry"), str) else None])))
    pay = ""
    salary = p.get("baseSalary") or {}
    if isinstance(salary, dict):
        value = salary.get("value") or {}
        if isinstance(value, dict) and (value.get("minValue") or value.get("value")):
            pay = f"{value.get('minValue') or value.get('value')}-{value.get('maxValue') or value.get('minValue') or value.get('value')} {salary.get('currency', '')} per {str(value.get('unitText', 'year')).lower()}"
    emp = p.get("employmentType")
    return {
        "id": p.get("identifier", {}).get("value") if isinstance(p.get("identifier"), dict) else p.get("url") or p.get("title"),
        "title": p.get("title", ""), "url": urljoin(page_url, p.get("url") or page_url), "location": "; ".join(filter(None, names)),
        "description": p.get("description") or "", "posted_at": p.get("datePosted"), "pay_text": pay,
        "remote": p.get("jobLocationType") == "TELECOMMUTE", "kind_hint": " ".join(emp) if isinstance(emp, list) else emp,
        "deadline": p.get("validThrough"),
    }


def _fetch_detail(ats: str, role: Dict[str, Any]) -> str:
    """Description for one role on boards whose list endpoint omits it."""
    url = role.get("detail")
    if not url:
        return ""
    r = http_get(url, html_page=(ats in ("yc", "html")))
    if not r or r.status_code != 200:
        return ""
    if ats == "greenhouse":
        return html_lib.unescape(r.json().get("content") or "")
    if ats == "smartrecruiters":
        sections = ((r.json().get("jobAd") or {}).get("sections") or {})
        return "\n\n".join(s.get("text", "") for s in sections.values() if isinstance(s, dict))
    if ats == "html":
        body = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", r.text)
        return re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", "\n", body))[:6000]
    if ats == "yc":
        m = re.search(r'data-page="([^"]+)"', r.text)
        if m:
            props = json.loads(html_lib.unescape(m.group(1))).get("props", {})
            job = props.get("job") or props.get("jobPosting") or {}
            role["posted_at"] = role.get("posted_at") or job.get("createdAt")
            if isinstance(job.get("skills"), list):
                role["skills"] = role.get("skills") or job["skills"]
            return (job.get("description") or "").replace("\\\n", "\n")
    return ""


# ---------------------------------------------------------------- relevance filter

def wanted_kind(title: str, kind_hint: str, kinds: List[str]) -> Optional[str]:
    """'internship' / 'job' if this role is worth storing for someone after these kinds, else None."""
    text = f"{title} {kind_hint or ''}"
    if INTERN_RE.search(text):
        return "internship" if "internship" in kinds else None
    if "job" in kinds and ENTRY_RE.search(title) and not SENIOR_RE.search(title):
        return "job"
    if "freelance" in kinds and re.search(r"contract|freelance|part[- ]time", kind_hint or "", re.I) and not SENIOR_RE.search(title):
        return "freelance"
    return None


# ---------------------------------------------------------------- discovery

ATS_LINK_RE = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([\w-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([\w-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([\w-]+)", re.I)),
    ("recruitee", re.compile(r"([\w-]+)\.recruitee\.com", re.I)),
    ("workday", re.compile(r"([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)", re.I)),
]
NOT_SLUGS = {"embed", "api", "jobs", "v1", "careers", "www", "static", "assets"}
CAREERS_LINK_RE = re.compile(r'href="([^"#?]*/(?:careers?|jobs|join-us|joinus|work-with-us|current-openings|openings|open-positions)/?)"', re.I)
NOT_ROLE_TEXT = re.compile(r"\b(how|why|what|will|blog|guide|feature|program(me)?|course|learn|webinar|story|stories|culture|benefits|life at|meet)\b", re.I)
ROLE_HREF = re.compile(r"career|job|opening|position|role|vacanc|apply|greenhouse|lever|ashby|workable|smartrecruiters|recruitee|myworkdayjobs", re.I)


def _norm(name: str) -> str:
    name = re.sub(r"\b(inc|llc|ltd|limited|pvt|private|technologies|technology|labs|software|solutions|india|corp|co|the|ai|hq)\b\.?", "", (name or "").lower())
    return re.sub(r"[^a-z0-9]", "", name)


def _same_company(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    return bool(na and nb) and (na == nb or (len(min(na, nb, key=len)) >= 4 and (na in nb or nb in na)))


def find_board_on_site(website: str) -> Optional[Tuple[str, str]]:
    """Read the homepage and its careers page for links to a hiring system or JobPosting data."""
    if not website:
        return None
    website = website if website.startswith("http") else f"https://{website}"
    pages = [website]
    r = http_get(website, html_page=True)
    if not r or r.status_code >= 400:
        return None
    hit = _ats_in_html(r.url) or _ats_in_html(r.text)  # job links often redirect straight to the board
    if hit:
        return hit
    if _jsonld_postings(r.text):
        return ("jsonld", r.url)
    home_host = urlparse(r.url).netloc.replace("www.", "")
    for href in CAREERS_LINK_RE.findall(r.text)[:6]:
        url = urljoin(r.url, html_lib.unescape(href))
        host = urlparse(url).netloc.replace("www.", "")
        if url in pages or not (host.endswith(home_host) or home_host.endswith(host)):
            hit = _ats_in_html(url)
            if hit:
                return hit
            continue
        pages.append(url)
        page = http_get(url, html_page=True)
        if not page or page.status_code >= 400:
            continue
        hit = _ats_in_html(page.text) or _ats_in_html(page.url)
        if hit:
            return hit
        if _jsonld_postings(page.text):
            return ("jsonld", page.url)
        if _html_jobs_found(page.text):
            return ("html", page.url)
    for path in ("/careers", "/jobs", "/careers/"):
        url = urljoin(r.url, path)
        if url in pages:
            continue
        pages.append(url)
        page = http_get(url, html_page=True)
        if not page or page.status_code >= 400 or page.url.rstrip("/") == r.url.rstrip("/"):
            continue
        hit = _ats_in_html(page.text) or _ats_in_html(page.url)
        if hit:
            return hit
        if _jsonld_postings(page.text):
            return ("jsonld", page.url)
        if _html_jobs_found(page.text):
            return ("html", page.url)
    return None


def _html_jobs_found(page: str) -> bool:
    """At least two links that read like job titles and point at job-like URLs."""
    hits = 0
    for href, inner in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', page, re.S | re.I):
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_lib.unescape(inner))).strip()
        if 6 <= len(text) <= 120 and ROLE_TEXT_RE.search(text) and not NOT_ROLE_TEXT.search(text) and ROLE_HREF.search(href):
            hits += 1
    return hits >= 2


def _ats_in_html(text: str) -> Optional[Tuple[str, str]]:
    for ats, rx in ATS_LINK_RE:
        for m in rx.finditer(text):
            if ats == "workday":
                return ("workday", f"{m.group(1)}|{m.group(2)}|{m.group(3)}")
            slug = m.group(1)
            if slug.lower() not in NOT_SLUGS:
                return (ats, slug)
    return None


def slug_candidates(name: str, website: Optional[str]) -> List[str]:
    out = []
    compact = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    hyphen = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    out += [compact, hyphen, _norm(name)]
    if website:
        host = urlparse(website if website.startswith("http") else f"https://{website}").netloc.lower().replace("www.", "")
        out.append(host.split(".")[0])
    return [s for i, s in enumerate(out) if s and len(s) >= 2 and s not in out[:i]]


def probe_boards(name: str, website: Optional[str]) -> Optional[Tuple[str, str]]:
    """Try each hiring system's public API with likely slugs; accept only verified or exact-name matches."""
    candidates = slug_candidates(name, website)
    root = ""
    if website:
        root = urlparse(website if website.startswith("http") else f"https://{website}").netloc.lower().replace("www.", "").split(".")[0]
    # Boards without a name to verify against only count on an exact name / domain match, and never for very short names.
    exact = {x for x in (_norm(name), root, re.sub(r"[^a-z0-9]", "", name.lower())) if len(x) >= 4}

    def try_ats(ats: str) -> Optional[Tuple[str, str]]:
        for slug in candidates:
            if ats == "greenhouse":
                r = http_get(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
                if r and r.status_code == 200 and _same_company(r.json().get("name", ""), name):
                    return (ats, slug)
            elif ats == "workable":
                # Workable answers with a name even for unused slugs, so require real postings.
                r = http_get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
                if r and r.status_code == 200 and r.json().get("jobs") and _same_company(r.json().get("name", ""), name):
                    return (ats, slug)
            elif ats == "smartrecruiters":
                for variant in {slug, name.replace(" ", ""), name.replace(" ", "").title()}:
                    r = http_get(f"https://api.smartrecruiters.com/v1/companies/{variant}/postings", params={"limit": 1})
                    if r and r.status_code == 200:
                        content = r.json().get("content") or []
                        if content and _same_company((content[0].get("company") or {}).get("name", ""), name):
                            return (ats, variant)
            elif _norm(slug) in exact or slug in exact:
                jobs = ADAPTERS[ats](slug)
                if jobs is not None:
                    return (ats, slug)
        return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        for hit in pool.map(try_ats, ["greenhouse", "lever", "ashby", "workable", "smartrecruiters", "recruitee"]):
            if hit:
                return hit
    return None


def guess_website(name: str) -> Optional[str]:
    """Try name.com / .ai / .in / .io / .co / .tech and keep the first whose page title mentions the name."""
    base = re.sub(r"[^a-z0-9]", "", name.lower())
    if len(base) < 3:
        return None
    for tld in ("com", "ai", "in", "io", "co", "tech", "dev", "app"):
        url = f"https://{base}.{tld}"
        try:
            _wait_turn(url)
            r = _session.get(url, timeout=8)
        except requests.RequestException:
            continue
        title = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.S | re.I)
        if r.status_code < 400 and title and _norm(name) in _norm(title.group(1)) + re.sub(r"[^a-z0-9]", "", title.group(1).lower()):
            return r.url
    return None


def discover(company: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    if not company.get("website") and company.get("origin") in ("user", "hn"):
        website = guess_website(company["name"])
        if website:
            company["website"] = website
            update_company(company["id"], website=website)
    return find_board_on_site(company.get("website")) or probe_boards(company["name"], company.get("website"))


# ---------------------------------------------------------------- seeding

def _yc_companies() -> List[Dict[str, Any]]:
    """YC company list (open dataset mirrored on GitHub), cached for a day."""
    if YC_CACHE.exists() and time.time() - YC_CACHE.stat().st_mtime < 86400:
        return json.loads(YC_CACHE.read_text(encoding="utf-8"))
    r = http_get(YC_DATASET)
    if not r or r.status_code != 200:
        return json.loads(YC_CACHE.read_text(encoding="utf-8")) if YC_CACHE.exists() else []
    slim = [{k: c.get(k) for k in ("name", "slug", "website", "all_locations", "regions", "isHiring", "status")} for c in r.json()]
    YC_CACHE.parent.mkdir(parents=True, exist_ok=True)
    YC_CACHE.write_text(json.dumps(slim), encoding="utf-8")
    return slim


HN_CACHE = ROOT / "data" / "hn_hiring_companies.json"


def _hn_hiring_companies() -> List[Dict[str, str]]:
    """Companies in this month's HN 'Who is hiring?' thread that mention interns, India or remote (cached daily)."""
    if HN_CACHE.exists() and time.time() - HN_CACHE.stat().st_mtime < 86400:
        return json.loads(HN_CACHE.read_text(encoding="utf-8"))
    r = http_get("https://hn.algolia.com/api/v1/search_by_date", params={"query": "Ask HN: Who is hiring?", "tags": "story,ask_hn", "hitsPerPage": 5})
    story = next((h for h in (r.json().get("hits", []) if r and r.status_code == 200 else []) if (h.get("title") or "").startswith("Ask HN: Who is hiring?")), None)
    if not story:
        return []
    c = http_get("https://hn.algolia.com/api/v1/search", params={"tags": f"comment,story_{story['objectID']}", "hitsPerPage": 1000})
    out = []
    for hit in (c.json().get("hits", []) if c and c.status_code == 200 else []):
        if str(hit.get("parent_id")) != str(story["objectID"]):
            continue
        text = html_lib.unescape(re.sub(r"<[^>]+>", " ", hit.get("comment_text") or ""))
        if not re.search(r"\bintern|\bindia\b|bengaluru|bangalore|\bremote\b", text, re.I):
            continue
        head = text.strip().split("|")[0].strip()
        name = re.sub(r"\s*\(.*?\)\s*", " ", head).strip()[:60]
        urls = [u for u in re.findall(r"https?://[^\s\"'<>)]+", html_lib.unescape(hit.get("comment_text") or "")) if "ycombinator.com" not in u]
        if 2 <= len(name) <= 40 and len(name.split()) <= 4:
            out.append({"name": name, "website": urls[0] if urls else None})
    HN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    HN_CACHE.write_text(json.dumps(out), encoding="utf-8")
    return out


def sync_seeds() -> int:
    """Make sure every seed company is in the table. Returns how many were added."""
    added = 0
    if SEED_FILE.exists():
        seeds = yaml.safe_load(SEED_FILE.read_text(encoding="utf-8")) or {}
        for group, rows in seeds.items():
            for row in rows or []:
                if add_company(row["name"], website=row.get("website"), origin="seed", location_hint=group):
                    added += 1
    for c in _hn_hiring_companies():
        if add_company(c["name"], website=c.get("website"), origin="hn", location_hint="HN Who is hiring"):
            added += 1
    for c in _yc_companies():
        where = f"{c.get('all_locations') or ''} {' '.join(c.get('regions') or [])}"
        india = INDIA_RE.search(where)
        fully_remote = "Fully Remote" in (c.get("regions") or [])
        if c.get("isHiring") and c.get("status") in ("Active", "Public") and fully_remote and not india:
            # Remote-first YC companies: only their YC page (cheap); location filters drop US-only roles.
            if add_company(c["name"], website=c.get("website"), origin="yc", location_hint="Fully remote", ats="yc", slug=c["slug"]):
                added += 1
        if c.get("isHiring") and c.get("status") in ("Active", "Public") and india:
            if add_company(c["name"], website=c.get("website"), origin="yc", location_hint=c.get("all_locations"), ats="yc", slug=c["slug"]):
                added += 1
            if add_company(c["name"], website=c.get("website"), origin="yc", location_hint=c.get("all_locations")):
                added += 1
    return added


# ---------------------------------------------------------------- polling

def _board_label(company: Dict[str, Any]) -> str:
    return f"{company['name']} careers" if company["ats"] != "yc" else f"{company['name']} on YC"


def poll_board(company: Dict[str, Any], kinds: List[str]) -> Dict[str, Any]:
    """Fetch one board, store fitting roles, close roles that vanished. Returns counts."""
    roles = ADAPTERS[company["ats"]](company["slug"])
    if roles is None:
        raise RuntimeError(f"{ATS_LABELS[company['ats']]} board '{company['slug']}' did not respond")
    items = []
    for role in roles:
        kind = wanted_kind(role.get("title", ""), role.get("kind_hint") or "", kinds)
        if not kind or not role.get("url"):
            continue
        description = role.get("description") or _fetch_detail(company["ats"], role)
        exp = None
        label = role.get("experience_label") or ""
        m = re.search(r"(\d+)\+?\s*years?", label)
        if m:
            exp = float(m.group(1))
        elif re.search(r"new grad|any|entry|intern", label, re.I):
            exp = 0.0
        pay = parse_pay(role.get("pay_text") or "", "INR" if INDIA_RE.search(role.get("location") or "") else "USD",
                        "month" if kind == "internship" else None) if role.get("pay_text") else None
        items.append(make_item(
            source="yc" if company["ats"] == "yc" else "careers",
            source_label=_board_label(company),
            external_id=f"{company['ats']}:{company['slug']}:{role['id']}",
            title=role["title"],
            company=company["name"],
            url=role["url"],
            description=description,
            locations=[role.get("location") or ""],
            posted_at=role.get("posted_at"),
            deadline=role.get("deadline"),
            skills=role.get("skills") or [],
            pay=pay or {},
            pay_text=role.get("pay_text") or "",
            kind=kind,
            remote=role.get("remote"),
            experience_years=exp,
            default_currency="INR" if INDIA_RE.search(role.get("location") or "") else "USD",
        ))
    started = datetime.now(timezone.utc).isoformat()
    new = upsert_opportunities(items, dedupe_key) if items else 0
    # The board lists every open role, so anything of ours from it that wasn't just seen has closed.
    prefix = f"{company['ats']}:{company['slug']}:"
    conn = get_db_connection()
    conn.execute(
        "UPDATE opportunities SET active = 0 WHERE external_id LIKE ? AND last_seen_at < ? AND active = 1",
        (prefix.replace("%", r"\%") + "%", started),
    )
    conn.commit()
    conn.close()
    return {"open_roles": len(roles), "matches": len(items), "new": new}


def _schedule(company: Dict[str, Any], new_matches: int) -> Dict[str, Any]:
    """Adaptive revisit: busy boards sooner (min 6h), quiet boards later (max 7 days)."""
    interval = company.get("check_interval_hours") or 24
    interval = max(6, interval / 2) if new_matches else min(168, interval * 1.5)
    nxt = datetime.now(timezone.utc) + timedelta(hours=interval)
    return {"check_interval_hours": interval, "next_check_at": nxt.isoformat()}


def process_company(company: Dict[str, Any], kinds: List[str]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    if not company.get("ats"):
        found = discover(company)
        if not found:
            retry_days = 7 if company.get("origin") == "user" else 30
            update_company(company["id"], discovery_state="not_found", last_checked_at=now.isoformat(),
                           next_check_at=(now + timedelta(days=retry_days)).isoformat(),
                           last_error="No public careers board found")
            return {"company": company["name"], "discovered": False}
        ats, slug = found
        # Another row may already cover this board (e.g. a YC company that also uses Greenhouse).
        conn = get_db_connection()
        dup = conn.execute("SELECT id FROM companies WHERE ats = ? AND slug = ? AND id != ?", (ats, slug, company["id"])).fetchone()
        conn.close()
        if dup:
            update_company(company["id"], discovery_state="duplicate", last_error=f"Same board as company #{dup[0]}",
                           next_check_at=(now + timedelta(days=365)).isoformat())
            return {"company": company["name"], "discovered": False}
        company.update(ats=ats, slug=slug)
        update_company(company["id"], ats=ats, slug=slug, discovery_state="found")
    try:
        counts = poll_board(company, kinds)
    except Exception as e:
        update_company(company["id"], last_error=str(e)[:200], last_checked_at=now.isoformat(),
                       next_check_at=(now + timedelta(hours=12)).isoformat())
        return {"company": company["name"], "error": str(e)[:200]}
    update_company(company["id"], last_checked_at=now.isoformat(), last_open_roles=counts["open_roles"],
                   last_matches=counts["matches"], last_error=None, **_schedule(company, counts["new"]))
    return {"company": company["name"], **counts}


def crawl_companies(kinds: List[str], budget_seconds: float = 120, max_companies: int = 60) -> Dict[str, Any]:
    """Work through due companies until the time budget runs out. Parallel across hosts, polite per host."""
    sync_seeds()
    due = due_companies(datetime.now(timezone.utc).isoformat(), max_companies)
    deadline = time.monotonic() + budget_seconds
    results: List[Dict[str, Any]] = []

    def run(company: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if time.monotonic() > deadline:
            return None
        try:
            return process_company(company, kinds)
        except Exception as e:
            logger.error(f"{company['name']}: {e}")
            return {"company": company["name"], "error": str(e)[:200]}

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = [r for r in pool.map(run, due) if r]
    return {
        "checked": len(results),
        "discovered": sum(1 for r in results if r.get("discovered") is not False and "open_roles" in r),
        "not_found": sum(1 for r in results if r.get("discovered") is False),
        "new": sum(r.get("new", 0) for r in results),
        "matches": sum(r.get("matches", 0) for r in results),
        "errors": [r for r in results if r.get("error")][:5],
        "remaining_due": max(0, len(due) - len(results)),
    }


def track_company(name: str, website: Optional[str] = None, kinds: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Watch a company the user asked for. Checks every board it has: its YC page if it's a YC company,
    plus its own careers board (found from the website or by probing hiring systems).
    """
    kinds = kinds or ["internship", "job"]
    yc = next((c for c in _yc_companies() if _norm(c["name"]) == _norm(name)), None)
    if yc:
        name, website = yc["name"], website or yc.get("website")
        add_company(name, website=website, origin="yc", location_hint=yc.get("all_locations"), ats="yc", slug=yc["slug"])

    def rows_for(n: str) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        all_rows = [dict(r) for r in conn.execute("SELECT * FROM companies WHERE discovery_state != 'duplicate'").fetchall()]
        conn.close()
        return [r for r in all_rows if _norm(r["name"]) == _norm(n)]

    rows = rows_for(name)
    if rows:
        name = next((r["name"] for r in rows if r["ats"] and r["ats"] != "yc"), rows[0]["name"])
    if not any(r["ats"] != "yc" for r in rows):
        add_company(name, website=website, origin="user")
        rows = rows_for(name)

    boards, totals = [], {"open_roles": 0, "matches": 0, "new": 0}
    for company in rows:
        if website and not company.get("website"):
            update_company(company["id"], website=website)
            company["website"] = website
        if not company.get("ats"):
            update_company(company["id"], discovery_state="pending")
        result = process_company(company, kinds)
        for k in totals:
            totals[k] += result.get(k, 0)
        conn = get_db_connection()
        fresh = dict(conn.execute("SELECT * FROM companies WHERE id = ?", (company["id"],)).fetchone())
        conn.close()
        if fresh.get("ats"):
            boards.append({"ats": fresh["ats"], "label": ATS_LABELS[fresh["ats"]], "slug": fresh["slug"], **{k: result.get(k) for k in totals}})
    return {"name": name, "website": website or next((r.get("website") for r in rows if r.get("website")), None),
            "boards": boards, **totals}
