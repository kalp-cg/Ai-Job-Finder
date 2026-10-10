"""
Turns raw postings from any source into one shape, and parses the fuzzy parts of job text:
skills (with synonyms), pay (any currency / period -> monthly USD), experience, work type, location, emails.
Pure functions, no network.
"""

from datetime import datetime, timezone
import html
import re
from typing import Any, Dict, List, Optional, Tuple

# Canonical skill -> regex of ways people write it.
SKILLS: Dict[str, str] = {
    "React": r"react(?:\.?js)?(?!\s*native)",
    "React Native": r"react[\s-]?native",
    "Next.js": r"next\.?js|nextjs",
    "Vue": r"vue(?:\.?js)?",
    "Angular": r"angular(?:js)?",
    "Svelte": r"svelte(?:kit)?",
    "JavaScript": r"javascript|\bjs\b|ecmascript",
    "TypeScript": r"typescript|\bts\b",
    "Node.js": r"node(?:\.?js)?\b",
    "Express": r"express(?:\.?js)?",
    "HTML/CSS": r"\bhtml5?\b|\bcss3?\b",
    "Tailwind": r"tailwind(?:\s*css)?",
    "Python": r"python",
    "Django": r"django",
    "FastAPI": r"fast\s?api",
    "Flask": r"flask",
    "Java": r"\bjava\b(?!\s*script)",
    "Spring": r"spring\s?boot|\bspring\b",
    "Kotlin": r"kotlin",
    "Swift": r"\bswift\b",
    "Flutter": r"flutter",
    "Go": r"\bgolang\b|\bgo\b(?=\s*(?:,|/|\)|developer|engineer|lang|and))",
    "Rust": r"\brust\b",
    "C++": r"c\+\+|\bcpp\b",
    "C#": r"c#|\.net\b|dotnet",
    "PHP": r"\bphp\b|laravel",
    "Ruby": r"\bruby\b|rails",
    "SQL": r"\bsql\b|mysql",
    "PostgreSQL": r"postgres(?:ql)?",
    "MongoDB": r"mongo(?:db)?",
    "Redis": r"redis",
    "GraphQL": r"graphql",
    "REST APIs": r"rest(?:ful)?\s*api",
    "Docker": r"docker|container",
    "Kubernetes": r"kubernetes|\bk8s\b",
    "AWS": r"\baws\b|amazon web services",
    "GCP": r"\bgcp\b|google cloud",
    "Azure": r"\bazure\b",
    "Linux": r"linux",
    "Git": r"\bgit\b|github",
    "Machine Learning": r"machine learning|\bml\b",
    "Deep Learning": r"deep learning|pytorch|tensorflow|keras",
    "LLMs": r"\bllms?\b|large language model|gpt|openai|gemini|anthropic|claude",
    "RAG": r"\brag\b|retrieval[- ]augmented|vector (?:db|database|search)",
    "NLP": r"\bnlp\b|natural language",
    "Computer Vision": r"computer vision|opencv",
    "Data Analysis": r"data analy(?:sis|tics)|pandas|numpy|excel",
    "Figma": r"figma",
}
_SKILL_RES = {name: re.compile(pattern, re.I) for name, pattern in SKILLS.items()}

FX_TO_USD = {"USD": 1.0, "INR": 1 / 88.0, "EUR": 1.08, "GBP": 1.27, "CAD": 0.73, "AUD": 0.66, "PLN": 0.25}
PERIOD_TO_MONTH = {"hour": 160.0, "day": 21.0, "week": 4.33, "month": 1.0, "year": 1 / 12.0}

KIND_RE = {
    "internship": re.compile(r"\bintern(?:ship)?s?\b|co-?op\b|apprentice", re.I),
    "freelance": re.compile(r"\bfreelanc\w*|\bcontract(?:or)?\b|\bgig\b|part[- ]time project", re.I),
}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMAIL_SKIP = re.compile(r"no-?reply|example\.|\.(png|jpg|gif)$", re.I)
REMOTE_RE = re.compile(r"\bremote\b|work from home|\bwfh\b|anywhere|worldwide", re.I)


def clean_text(raw: Optional[str]) -> str:
    """Strip HTML to readable plain text."""
    if not raw:
        return ""
    text = re.sub(r"<\s*(br|/p|/li|/h\d|/div)\s*/?>", "\n", raw, flags=re.I)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def extract_skills(*texts: str) -> List[str]:
    blob = " ".join(t for t in texts if t)
    return [name for name, rx in _SKILL_RES.items() if rx.search(blob)]


def canonical_skill(term: str) -> Optional[str]:
    """Map a user-typed skill ('reactjs', 'node') to its canonical name."""
    for name, rx in _SKILL_RES.items():
        if rx.fullmatch(term.strip()) or name.lower() == term.strip().lower():
            return name
    found = extract_skills(term)
    return found[0] if len(found) == 1 else None


def detect_kind(title: str, text: str = "", default: str = "job") -> str:
    if KIND_RE["internship"].search(title):
        return "internship"
    if KIND_RE["freelance"].search(title):
        return "freelance"
    if KIND_RE["internship"].search(text[:600]):
        return "internship"
    return default


APPLY_CONTEXT = re.compile(r"\b(resume|résumé|cv|application|apply|applying|portfolio|github|cover letter|interested|send us|email us|to apply)\b", re.I)
NOT_APPLY_CONTEXT = re.compile(r"accommodat|disabilit|privacy|eeo|equal opportunit|compliance|benefits|fraud|scam|legal|data protection|gdpr|questions about|press|media|support@", re.I)
APPLY_LOCAL = re.compile(r"^(jobs?|careers?|hiring|hr|talent|recruit\w*|apply|applications?|work|team|founders?|internships?)$", re.I)
BAD_LOCAL = re.compile(r"accommodat|eeo|privacy|legal|compliance|benefit|support|noreply|no-reply|abuse|security|press", re.I)


def find_email(text: str) -> Optional[str]:
    """
    An address to send an application to, or None.
    Only counts when the surrounding words are about applying, so HR-accommodation,
    privacy and EEO addresses found in boilerplate are never returned.
    """
    deobfuscated = re.sub(r"\s*[\[(]\s*at\s*[\])]\s*", "@", text or "", flags=re.I)
    deobfuscated = re.sub(r"\s*[\[(]\s*dot\s*[\])]\s*", ".", deobfuscated, flags=re.I)
    for m in EMAIL_RE.finditer(deobfuscated):
        email = m.group(0).rstrip(".")
        local = email.split("@")[0]
        if EMAIL_SKIP.search(email) or BAD_LOCAL.search(local):
            continue
        window = deobfuscated[max(0, m.start() - 160): m.end() + 60]
        if NOT_APPLY_CONTEXT.search(window):
            continue
        if APPLY_CONTEXT.search(window) or APPLY_LOCAL.match(local):
            return email
    return None


def is_remote(*texts: str) -> bool:
    return any(t and REMOTE_RE.search(t) for t in texts)


def _amount(number: str, suffix: str) -> float:
    value = float(number.replace(",", ""))
    suffix = (suffix or "").lower()
    if suffix == "k":
        value *= 1_000
    elif suffix in ("l", "lakh", "lakhs", "lac", "lpa"):
        value *= 100_000
    elif suffix in ("m", "mn"):
        value *= 1_000_000
    return value


_CURRENCY = r"(?P<cur>\$|usd|us\$|₹|rs\.?|inr|€|eur|£|gbp|cad|aud|pln)?"
_NUMBER = r"(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<suf>k|lpa|lakhs?|lac|l|mn?)?\b"
_PERIOD = r"(?:\s*(?:/|per|a|an)\s*(?P<per>hour|hr|h|day|week|wk|month|mo|year|yr|annum))?"
PAY_RE = re.compile(_CURRENCY + r"\s?" + _NUMBER + r"(?:\s*(?:-|–|to)\s*" + _CURRENCY.replace("cur", "cur2") + r"\s?(?P<num2>\d[\d,]*(?:\.\d+)?)\s*(?P<suf2>k|lpa|lakhs?|lac|l|mn?)?)?"
    + r"(?:\s*(?P<cur3>usd|inr|eur|gbp|pln|cad|aud)\b)?" + _PERIOD, re.I)

_CUR_MAP = {"$": "USD", "usd": "USD", "us$": "USD", "₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR",
            "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP", "cad": "CAD", "aud": "AUD", "pln": "PLN"}
_PER_MAP = {"hour": "hour", "hr": "hour", "h": "hour", "day": "day", "week": "week", "wk": "week",
            "month": "month", "mo": "month", "year": "year", "yr": "year", "annum": "year"}


def parse_pay(text: str, default_currency: str = "USD", default_period: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Find the first pay figure in text. Returns {min, max, currency, period, monthly_usd} or None.
    'LPA' implies yearly INR. A figure needs a currency or a period (or LPA) to count, so '3 years' is ignored.
    """
    for m in PAY_RE.finditer(text or ""):
        cur = (m.group("cur") or m.group("cur2") or m.group("cur3") or "").lower()
        suf = (m.group("suf") or "").lower()
        per = _PER_MAP.get((m.group("per") or "").lower())
        if suf == "lpa":
            cur, per = cur or "inr", "year"
        if not cur and not per:
            continue
        low = _amount(m.group("num"), suf)
        high = _amount(m.group("num2"), m.group("suf2") or suf) if m.group("num2") else low
        currency = _CUR_MAP.get(cur, default_currency)
        period = per or default_period or ("hour" if low < 500 else "month" if low < 20_000 else "year")
        if low <= 0:
            continue
        mid = (low + high) / 2
        return {
            "min": low,
            "max": high,
            "currency": currency,
            "period": period,
            "monthly_usd": round(mid * PERIOD_TO_MONTH[period] * FX_TO_USD.get(currency, 1.0), 2),
        }
    return None


EXP_RE = re.compile(r"(?P<n>\d+(?:\.\d+)?)\s*\+?\s*(?:-\s*\d+\s*)?(?P<unit>years?|yrs?|months?)\b(?:\s+of)?(?:\s+\w+){0,3}?\s+(?:experience|exp)\b", re.I)


def parse_experience(text: str) -> Optional[float]:
    """Minimum years of experience asked for, if stated."""
    if re.search(r"\bfresher|no experience|0 years?\b", text or "", re.I):
        return 0.0
    m = EXP_RE.search(text or "")
    if not m:
        return None
    n = float(m.group("n"))
    return round(n / 12, 2) if m.group("unit").lower().startswith("month") else n


def to_iso(value: Any) -> Optional[str]:
    """Accept unix seconds or ISO strings; return ISO UTC."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    rel = re.fullmatch(r"(?:about |over |almost )?(\d+|an?|one) (minute|hour|day|week|month|year)s?(?: ago)?", str(value).strip(), re.I)
    if rel:  # YC-style relative ages, e.g. "3 months"
        from datetime import timedelta
        n = 1 if rel.group(1).lower() in ("a", "an", "one") else int(rel.group(1))
        days = {"minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30, "year": 365}[rel.group(2).lower()]
        return (datetime.now(timezone.utc) - timedelta(days=n * days)).isoformat()
    text = re.sub(r"\s*GMT([+-]\d\d)(\d\d)$", r"\1:\2", str(value).strip()).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def make_item(
    *,
    source: str,
    source_label: str,
    external_id: str,
    title: str,
    company: str,
    url: str,
    description: str = "",
    locations: Optional[List[str]] = None,
    posted_at: Any = None,
    deadline: Any = None,
    skills: Optional[List[str]] = None,
    pay_text: str = "",
    pay: Optional[Dict[str, Any]] = None,
    kind: Optional[str] = None,
    remote: Optional[bool] = None,
    experience_years: Optional[float] = None,
    default_currency: str = "USD",
) -> Dict[str, Any]:
    """Build the normalized item every source returns."""
    description = clean_text(description)
    locations = [l for l in (locations or []) if l]
    location = ", ".join(locations[:3]) + (f" +{len(locations) - 3}" if len(locations) > 3 else "")
    kind = kind or detect_kind(title, description)
    if pay is None:
        pay = parse_pay(pay_text, default_currency) or parse_pay(description[:3000], default_currency)
    pay = pay or None  # callers pass {} to mean "pay not stated, don't guess from the description"
    return {
        "source": source,
        "source_label": source_label,
        "external_id": str(external_id),
        "title": title.strip(),
        "company": (company or "").strip() or "Unknown company",
        "url": url,
        "description": description[:12000],
        "location": location,
        "remote": bool(remote) if remote is not None else is_remote(location, title, description[:1500]),
        "kind": kind,
        "skills": sorted(set((skills or []) + extract_skills(title, description))),
        "pay_text": pay_text or (format_pay(pay) if pay else ""),
        "pay_monthly_usd": pay["monthly_usd"] if pay else None,
        "experience_years": experience_years if experience_years is not None else parse_experience(description),
        "contact_email": find_email(description),
        "posted_at": to_iso(posted_at),
        "deadline": to_iso(deadline),
    }


def format_pay(pay: Dict[str, Any]) -> str:
    symbol = {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£"}.get(pay["currency"], pay["currency"] + " ")
    fmt = lambda v: f"{round(v / 1000):g}k" if v >= 10_000 else f"{v:,.0f}" if v >= 1000 else f"{v:g}"
    span = fmt(pay["min"]) if pay["min"] == pay["max"] else f"{fmt(pay['min'])}–{fmt(pay['max'])}"
    return f"{symbol}{span}/{ {'hour': 'hr', 'month': 'mo', 'year': 'yr'}.get(pay['period'], pay['period']) }"


def dedupe_key(company: str, title: str) -> str:
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
    title_n = re.sub(r"\b(20\d\d|summer|fall|winter|spring|remote|hybrid|i|ii|iii)\b", "", norm(title))
    return f"{norm(company)}|{re.sub(r'\s+', ' ', title_n).strip()}"
