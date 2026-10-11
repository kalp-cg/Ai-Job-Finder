"""
Public, documented job & internship feeds. Every source here is either an official public API
or a published dataset, and each one's terms (credit the source, link to the original posting,
modest request rates) are respected via SOURCES[...]['min_interval_minutes'] and source_label.

Deliberately NOT included (their terms or robots.txt forbid automated access):
LinkedIn, Internshala, Naukri, Wellfound/AngelList, Indeed, Glassdoor. SARTHI never fetches their pages.
Jobs from LinkedIn, Internshala and Naukri arrive only through the job-alert emails they send to the user,
read from the user's own inbox (see email_alerts.py).
"""

import logging
from typing import Any, Callable, Dict, List, Optional

import requests

from src.opportunities.email_alerts import fetch_email_alerts
from src.opportunities.normalize import make_item, parse_pay

logger = logging.getLogger("sarthi.sources")

HEADERS = {"User-Agent": "SARTHI/1.0 (personal job search assistant; low-frequency)"}
TIMEOUT = 25


def _get(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    resp = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------- SimplifyJobs (GitHub dataset)

SIMPLIFY_URL = "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json"


def fetch_simplify(query: Optional[str] = None) -> List[Dict[str, Any]]:
    """Community-maintained list of ~17k tech internships (4-5k active). Titles + locations only."""
    import time
    cutoff = time.time() - 60 * 86400
    items = []
    for row in _get(SIMPLIFY_URL):
        if not row.get("active") or not row.get("is_visible", True) or (row.get("date_posted") or 0) < cutoff:
            continue
        items.append(make_item(
            source="simplify",
            source_label="SimplifyJobs",
            external_id=row["id"],
            title=row.get("title", ""),
            company=row.get("company_name", ""),
            url=row.get("url", ""),
            description=" · ".join(filter(None, [row.get("category"), ", ".join(row.get("terms") or []), f"Sponsorship: {row.get('sponsorship')}"])),
            locations=row.get("locations"),
            posted_at=row.get("date_posted"),
            kind="internship",
        ))
    return items


# ---------------------------------------------------------------- The Muse (public API, 500 req/hr)

MUSE_URL = "https://www.themuse.com/api/public/jobs"
MUSE_CATEGORIES = ["Software Engineering", "Data and Analytics", "Data Science", "Design and UX", "IT"]
MUSE_LOCATIONS = ["Flexible / Remote", "Bengaluru, India", "Hyderabad, India", "Pune, India",
                  "Mumbai, India", "Gurgaon, India", "Noida, India", "Chennai, India", "New Delhi, India"]


def fetch_muse(query: Optional[str] = None) -> List[Dict[str, Any]]:
    passes = [
        {"level": "Internship", "category": MUSE_CATEGORIES, "pages": 5},
        {"level": "Internship", "location": MUSE_LOCATIONS, "pages": 3},
        {"level": "Entry Level", "category": ["Software Engineering"], "location": MUSE_LOCATIONS, "pages": 2},
    ]
    items, seen = [], set()
    for p in passes:
        for page in range(p.pop("pages")):
            data = _get(MUSE_URL, {**p, "page": page})
            for row in data.get("results", []):
                if row["id"] in seen:
                    continue
                seen.add(row["id"])
                levels = [l.get("name", "") for l in row.get("levels", [])]
                items.append(make_item(
                    source="muse",
                    source_label="The Muse",
                    external_id=row["id"],
                    title=row.get("name", ""),
                    company=(row.get("company") or {}).get("name", ""),
                    url=(row.get("refs") or {}).get("landing_page", ""),
                    description=row.get("contents", ""),
                    locations=[l.get("name") for l in row.get("locations", [])],
                    posted_at=row.get("publication_date"),
                    kind="internship" if "Internship" in levels else "job",
                ))
            if page + 1 >= data.get("page_count", 0):
                break
    return items


# ---------------------------------------------------------------- Unstop (India; robots.txt allows /api/public/*)

UNSTOP_URL = "https://unstop.com/api/public/opportunity/search-result"
UNSTOP_TERMS = ["developer", "software", "web development", "react", "python", "data science", "machine learning"]


def fetch_unstop(query: Optional[str] = None) -> List[Dict[str, Any]]:
    items, seen = [], set()
    for term in ([query] if query else UNSTOP_TERMS):
        data = _get(UNSTOP_URL, {"opportunity": "internships", "oppstatus": "open", "per_page": 30, "searchTerm": term})
        for row in (data.get("data") or {}).get("data", []):
            if row["id"] in seen or not row.get("regn_open"):
                continue
            seen.add(row["id"])
            jd = row.get("jobDetail") or {}
            pay = None
            if jd.get("paid_unpaid") == "unpaid":
                pay_text = "Unpaid"
                pay = {"min": 0, "max": 0, "currency": "INR", "period": "month", "monthly_usd": 0.0}
            elif jd.get("min_salary") or jd.get("max_salary"):
                low = float(jd.get("min_salary") or jd.get("max_salary"))
                high = float(jd.get("max_salary") or low)
                period = {"monthly": "month", "yearly": "year", "hourly": "hour"}.get(jd.get("pay_in"), "month")
                pay = parse_pay(f"₹{low:g}-{high:g} per {period}", "INR")
                pay_text = ""
            else:
                pay_text = ""
            locations = [l.get("city") for l in (jd.get("locations") or row.get("locations") or []) if isinstance(l, dict)]
            items.append(make_item(
                source="unstop",
                source_label="Unstop",
                external_id=row["id"],
                title=row.get("title", ""),
                company=(row.get("organisation") or {}).get("name", ""),
                url=row.get("seo_url") or f"https://unstop.com/{row.get('public_url', '')}",
                description=row.get("details", ""),
                locations=locations or (["Remote (India)"] if jd.get("type") == "wfh" else ["India"]),
                posted_at=row.get("approved_date") or row.get("updated_at"),
                deadline=row.get("end_date"),
                skills=[s.get("skill_name") or s.get("skill") for s in row.get("required_skills") or []],
                pay=pay,
                pay_text=pay_text,
                kind="internship",
                remote=jd.get("type") == "wfh",
                experience_years=jd.get("min_experience"),
                default_currency="INR",
            ))
    return items


# ---------------------------------------------------------------- Remote job boards with public APIs

def fetch_remotive(query: Optional[str] = None) -> List[Dict[str, Any]]:
    """Remotive asks for at most ~4 calls/day; credit 'Remotive' and link to its URL."""
    data = _get("https://remotive.com/api/remote-jobs", {"category": "software-dev"})
    return [make_item(
        source="remotive",
        source_label="Remotive",
        external_id=row["id"],
        title=row.get("title", ""),
        company=row.get("company_name", ""),
        url=row.get("url", ""),
        description=row.get("description", ""),
        locations=[row.get("candidate_required_location") or "Remote"],
        posted_at=row.get("publication_date"),
        pay_text=row.get("salary") or "",
        kind={"freelance": "freelance", "contract": "freelance", "internship": "internship"}.get(row.get("job_type")),
        remote=True,
    ) for row in data.get("jobs", [])]


def fetch_remoteok(query: Optional[str] = None) -> List[Dict[str, Any]]:
    """Remote OK: credit 'Remote OK' and link back to its URL."""
    rows = _get("https://remoteok.com/api")[1:]
    items = []
    for row in rows:
        pay = None
        if row.get("salary_min"):
            pay = parse_pay(f"${row['salary_min']}-{row.get('salary_max') or row['salary_min']} per year")
        items.append(make_item(
            source="remoteok",
            source_label="Remote OK",
            external_id=row["id"],
            title=row.get("position", ""),
            company=row.get("company", ""),
            url=row.get("url", ""),
            description=row.get("description", ""),
            locations=[row.get("location") or "Remote"],
            posted_at=row.get("epoch"),
            skills=[],
            pay=pay,
            remote=True,
        ))
    return items


def fetch_jobicy(query: Optional[str] = None) -> List[Dict[str, Any]]:
    """Jobicy: credit Jobicy and send applications to the original URL."""
    data = _get("https://jobicy.com/api/v2/remote-jobs", {"count": 100, "industry": "engineering"})
    items = []
    for row in data.get("jobs", []):
        pay = None
        if row.get("salaryMin"):
            period = {"yearly": "year", "monthly": "month", "hourly": "hour"}.get(row.get("salaryPeriod"), "year")
            pay = parse_pay(f"{row['salaryMin']}-{row.get('salaryMax') or row['salaryMin']} {row.get('salaryCurrency', 'USD')} per {period}")
        types = " ".join(row.get("jobType") or [])
        items.append(make_item(
            source="jobicy",
            source_label="Jobicy",
            external_id=row["id"],
            title=row.get("jobTitle", ""),
            company=row.get("companyName", ""),
            url=row.get("url", ""),
            description=row.get("jobDescription", ""),
            locations=[row.get("jobGeo") or "Remote"],
            posted_at=row.get("pubDate"),
            pay=pay,
            kind="internship" if "Intern" in types else "freelance" if ("Contract" in types or "Freelance" in types) else None,
            remote=True,
        ))
    return items


def fetch_himalayas(query: Optional[str] = None) -> List[Dict[str, Any]]:
    """Himalayas public API: 20 jobs per page, cursor pagination."""
    items, cursor = [], None
    for _ in range(5):
        params = {"limit": 20, **({"cursor": cursor} if cursor else {})}
        if query:
            data = _get("https://himalayas.app/jobs/api/search", {"q": query, **params})
        else:
            data = _get("https://himalayas.app/jobs/api", params)
        for row in data.get("jobs", []):
            pay = None
            if row.get("minSalary"):
                period = {"hourly": "hour", "monthly": "month", "yearly": "year", "annual": "year"}.get(row.get("salaryPeriod"), "year")
                pay = parse_pay(f"{row['minSalary']}-{row.get('maxSalary') or row['minSalary']} {row.get('currency', 'USD')} per {period}")
            emp = row.get("employmentType") or ""
            items.append(make_item(
                source="himalayas",
                source_label="Himalayas",
                external_id=row.get("guid") or row.get("applicationLink") or row.get("title"),
                title=row.get("title", ""),
                company=row.get("companyName", ""),
                url=row.get("applicationLink") or row.get("guid") or "",
                description=row.get("description") or row.get("excerpt") or "",
                locations=row.get("locationRestrictions") or ["Remote"],
                posted_at=row.get("pubDate"),
                pay=pay,
                kind="internship" if "Intern" in emp else "freelance" if emp in ("Contractor", "Part Time") else None,
                remote=True,
            ))
        cursor = data.get("nextCursor")
        if not cursor:
            break
    return items


# ---------------------------------------------------------------- Registry

SOURCES: Dict[str, Dict[str, Any]] = {
    "simplify": {"label": "SimplifyJobs", "fetch": fetch_simplify, "min_interval_minutes": 60,
                 "about": "Curated tech internships (mostly US), updated daily on GitHub."},
    "muse": {"label": "The Muse", "fetch": fetch_muse, "min_interval_minutes": 60,
             "about": "Internships and entry-level roles, including India and remote."},
    "unstop": {"label": "Unstop", "fetch": fetch_unstop, "min_interval_minutes": 60,
               "about": "Indian internships with stipends and deadlines (public API)."},
    "remotive": {"label": "Remotive", "fetch": fetch_remotive, "min_interval_minutes": 360,
                 "about": "Remote dev jobs. Remotive asks for at most 4 calls a day."},
    "remoteok": {"label": "Remote OK", "fetch": fetch_remoteok, "min_interval_minutes": 120,
                 "about": "Remote jobs with salary ranges."},
    "jobicy": {"label": "Jobicy", "fetch": fetch_jobicy, "min_interval_minutes": 120,
               "about": "Remote engineering jobs."},
    "himalayas": {"label": "Himalayas", "fetch": fetch_himalayas, "min_interval_minutes": 120,
                  "about": "Remote jobs with location and salary details."},
    "alerts": {"label": "Job alert emails", "fetch": fetch_email_alerts, "min_interval_minutes": 30,
               "about": "Jobs from the LinkedIn, Internshala and Naukri alert emails in your inbox, read-only. "
                        "SARTHI never opens those sites. Uses the Gmail app password from .env."},
}

FetchFn = Callable[[Optional[str]], List[Dict[str, Any]]]
