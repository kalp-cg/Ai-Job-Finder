"""
Ranking without LLM calls, so thousands of postings can be scored on every search.

score (0-10) = 10 x relevance^0.7 x (0.55 + 0.45 x fit) x (0.7 + 0.3 x source trust)
  where fit blends pay fit (15), location fit (15), freshness (15) and experience fit (10).

- relevance: skill coverage (query skills found in the posting's skills / title) blended with
  BM25 over title (x3), skills (x2) and description for any free-text words.
- pay fit: posting's monthly pay vs. what you asked for (unknown pay = neutral).
- freshness: half-life of 10 days from the posting date.
Hard filters: kind, remote-only, closed/expired postings, unpaid when you asked for pay.
Cross-source duplicates (same company + title) collapse to the best-scored copy.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from src.opportunities.normalize import FX_TO_USD, canonical_skill, extract_skills, parse_pay

STOPWORDS = set("""a an and are as at be by for from i in is it me my need of on or show the to with want looking
find search get give any some please job jobs role roles opportunity opportunities position positions work
experience exp expected expect salary stipend pay year years yr yrs month months per k lpa remote intern
internship internships freelance contract fulltime full time part""".split())

INDIA_WORDS = ["india", "bengaluru", "bangalore", "hyderabad", "pune", "mumbai", "gurgaon", "gurugram",
               "noida", "chennai", "delhi", "kolkata", "ahmedabad", "jaipur", "kochi", "chandigarh"]
LOCATION_WORDS = INDIA_WORDS + ["usa", "us", "uk", "europe", "canada", "germany", "singapore", "london",
                                "new york", "san francisco", "berlin", "dubai"]


TECH_ROLE_RE = re.compile(r"engineer|engineering|developer|\bdev\b|software|programmer|\bsde\b|full[- ]?stack|front[- ]?end|back[- ]?end|\bweb\b|mobile|android|\bios\b|data scien|data engineer|machine learning|\bml\b|\bai\b|devops|cloud|platform|\bqa\b|\bsdet\b|research|scientist|security|computer vision|nlp|coding|python|react|java|node|golang|rust", re.I)
OFF_ROLE_RE = re.compile(r"hardware|firmware|mechanical|electrical|civil|chemical|manufactur|sales|marketing|account(ant|ing)|customer|recruit|talent acquisition|\bhr\b|human resource|finance|legal|nurs|clinical|supply chain|operations|\bops\b|logistics|procurement|transcription|annotat|writer|content|video|editor|graphic|social media|influencer|brand|business development|\bbda\b|founder.?s office|ceo.?s office|management trainee|teacher|tutor|community|partnership", re.I)
SENIOR_RE = re.compile(r"\b(senior|sr\.?|staff|principal|lead|manager|director|head|vp|architect)\b", re.I)


def remote_open_to(location: str, wanted: List[str]) -> bool:
    """A remote posting is open to you unless it is restricted to places you didn't ask for."""
    loc = (location or "").lower()
    if not loc or loc in ("remote", "remote (india)") or re.search(r"worldwide|anywhere|global|international", loc):
        return True
    if "india" in wanted and any(re.search(rf"\b{w}\b", loc) for w in INDIA_WORDS):
        return True
    return any(re.search(rf"\b{re.escape(w)}\b", loc) for w in wanted if w != "remote")


# How much to trust each source before any outcomes are known (0-1). Direct-from-company beats aggregators.
SOURCE_PRIOR = {"careers": 1.0, "yc": 0.95, "alerts": 0.9, "simplify": 0.85, "hn": 0.8, "muse": 0.8, "reddit": 0.7,
                "remotive": 0.7, "remoteok": 0.7, "jobicy": 0.7, "himalayas": 0.7, "unstop": 0.45}
RESPONDED = {"replied", "won", "interviewing", "offer"}
APPLIED = RESPONDED | {"contacted", "applied", "rejected", "lost"}


def learned_trust(rows: List[Dict[str, Any]], strength: float = 6.0) -> Dict[str, float]:
    """
    Blend each source's prior with how often your own applications from it got a response.
    Beta-style smoothing: trust = (responses + strength*prior) / (applications + strength),
    so a source needs several real outcomes before it moves far from its prior.
    """
    applied: Dict[str, int] = {}
    responded: Dict[str, int] = {}
    for row in rows:
        if row.get("status") in APPLIED:
            src = row.get("source")
            applied[src] = applied.get(src, 0) + 1
            responded[src] = responded.get(src, 0) + (row.get("status") in RESPONDED)
    trust = dict(SOURCE_PRIOR)
    for src, n in applied.items():
        prior = SOURCE_PRIOR.get(src, 0.7)
        # Typical response rates are far below 100%, so scale observed rate by an expected ~25% baseline.
        observed = min(1.0, (responded.get(src, 0) / n) / 0.25)
        trust[src] = (observed * n + strength * prior) / (n + strength)
    return trust


@dataclass
class Query:
    raw: str = ""
    skills: List[str] = field(default_factory=list)
    terms: List[str] = field(default_factory=list)
    kinds: List[str] = field(default_factory=list)
    locations: List[str] = field(default_factory=list)
    remote_only: bool = False
    min_monthly_usd: Optional[float] = None
    pay_label: Optional[str] = None
    experience_years: Optional[float] = None
    max_age_days: int = 45
    note: Optional[str] = None
    trust: Optional[Dict[str, float]] = None

    def chips(self) -> List[str]:
        out = []
        out += [k.capitalize() for k in self.kinds]
        out += self.skills
        out += [t for t in self.terms]
        if self.remote_only:
            out.append("Remote only")
        out += [l.title() for l in self.locations]
        if self.experience_years is not None:
            out.append(f"{self.experience_years:g} yr experience")
        if self.pay_label:
            out.append(f"{self.pay_label}+")
        return out


def parse_query(text: str, profile: Optional[Dict[str, Any]] = None, inr_per_usd: float = 88.0) -> Query:
    """Turn 'react internship, 1 year exp, expecting 20k/month, remote' into a Query."""
    profile = profile or {}
    prefs = profile.get("search") or {}
    lower = f" {text.lower()} "
    q = Query(raw=text)

    if re.search(r"\bintern(ship)?s?\b", lower):
        q.kinds.append("internship")
    if re.search(r"\bfreelanc\w*|\bcontract\w*|\bgig\b", lower):
        q.kinds.append("freelance")
    if re.search(r"full[- ]?time|\bjob\b(?!s? (for|in) intern)|new grad|entry[- ]level", lower) and not q.kinds:
        q.kinds.append("job")

    q.skills = extract_skills(text)
    if "react native" in lower and "React" in q.skills and not re.search(r"react(?!\s*native)", lower):
        q.skills.remove("React")

    q.remote_only = bool(re.search(r"\bremote only\b|\bonly remote\b|\bfully remote\b|\bwfh\b", lower))
    if re.search(r"\bremote\b", lower) and not q.remote_only:
        q.locations.append("remote")
    for loc in LOCATION_WORDS:
        if re.search(rf"\b{re.escape(loc)}\b", lower):
            q.locations.append(loc)

    exp = re.search(r"(\d+(?:\.\d+)?)\s*\+?\s*(years?|yrs?|months?)\s*(?:of\s*)?(?:exp|experience)?", lower)
    if re.search(r"\bfresher\b|no experience|\b0 exp", lower):
        q.experience_years = 0.0
    elif exp and re.search(r"exp|experience|year|yr|month", exp.group(0)):
        n = float(exp.group(1))
        q.experience_years = round(n / 12, 2) if exp.group(2).startswith("month") else n

    # Pay: strip the experience phrase first so "1 year" isn't read as money.
    pay_text = lower.replace(exp.group(0), " ") if exp else lower
    currency = (prefs.get("currency") or ("INR" if any(w in lower for w in INDIA_WORDS) else "INR" if "₹" in text else "USD"))
    period = "month" if "internship" in q.kinds or re.search(r"stipend|/\s*mo|per month|monthly", lower) else None
    pay = parse_pay(pay_text, currency, period)
    if not pay:
        bare = (re.search(r"(?:salary|stipend|pay|expect\w*|ctc)\D{0,15}(\d[\d,]*(?:\.\d+)?)\s*(k|lpa|l|lakhs?)?", pay_text)
                or re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(k|lpa|lakhs?)\b", pay_text))
        if bare:
            suffix = bare.group(2) or ""
            per = "year" if suffix in ("lpa",) else (period or "month")
            pay = parse_pay(f"{currency} {bare.group(1)}{suffix} per {per}", currency)
    if pay:
        rate = {**FX_TO_USD, "INR": 1 / inr_per_usd}
        q.min_monthly_usd = pay["min"] * {"hour": 160, "day": 21, "week": 4.33, "month": 1, "year": 1 / 12}[pay["period"]] * rate.get(pay["currency"], 1)
        symbol = {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£"}.get(pay["currency"], "")
        unit = {"hour": "hr", "month": "mo", "year": "yr"}.get(pay["period"], pay["period"])
        q.pay_label = f"{symbol}{pay['min']:,.0f}/{unit}"

    # Anything the question doesn't say falls back to the profile's search preferences.
    if not q.locations and not q.remote_only and prefs.get("locations"):
        q.locations = [l.lower() for l in prefs["locations"]]
    if q.experience_years is None and prefs.get("experience_years") is not None:
        q.experience_years = float(prefs["experience_years"])

    skill_words = {w for s in q.skills for w in re.split(r"[^a-z0-9+#]+", s.lower()) if w}
    for word in re.findall(r"[a-z][a-z0-9+#.-]{2,}", lower):
        if word not in STOPWORDS and word not in skill_words and word not in LOCATION_WORDS and not canonical_skill(word):
            if word in {"frontend", "backend", "fullstack", "full-stack", "web", "mobile", "data", "ml", "ai", "design", "devops", "cloud", "security", "android", "ios", "product", "analyst", "developer", "engineer", "software"}:
                q.terms.append(word)
    return q


def profile_query(profile: Dict[str, Any], inr_per_usd: float = 88.0) -> Query:
    """The standing query used to fill the New queue from the user's profile."""
    prefs = profile.get("search") or {}
    skills_cfg = profile.get("skills") or {}
    skills = [s for group in skills_cfg.values() for s in (group or [])] if isinstance(skills_cfg, dict) else list(skills_cfg)
    canonical = []
    for s in skills:
        c = canonical_skill(s)
        if c and c not in canonical:
            canonical.append(c)
    q = Query(raw="profile", skills=canonical[:12], kinds=list(prefs.get("kinds") or ["internship"]))
    roles = " ".join((profile.get("preferences") or {}).get("roles") or [])
    q.terms = [w for w in re.findall(r"[a-z]+", roles.lower()) if w not in ("engineer", "developer")]
    q.locations = [l.lower() for l in prefs.get("locations") or []]
    q.remote_only = bool(prefs.get("remote_only"))
    if prefs.get("experience_years") is not None:
        q.experience_years = float(prefs["experience_years"])
    if prefs.get("expected_monthly"):
        rate = {**FX_TO_USD, "INR": 1 / inr_per_usd}
        cur = prefs.get("currency", "USD")
        q.min_monthly_usd = float(prefs["expected_monthly"]) * rate.get(cur, 1)
        q.pay_label = f"{ {'INR': '₹', 'USD': '$'}.get(cur, cur) }{float(prefs['expected_monthly']):,.0f}/mo"
    return q


# ---------------------------------------------------------------- scoring

def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9+#]+", (text or "").lower())


class BM25:
    def __init__(self, docs: List[List[str]], k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = docs
        self.avgdl = sum(map(len, docs)) / max(1, len(docs))
        df: Dict[str, int] = {}
        for d in docs:
            for t in set(d):
                df[t] = df.get(t, 0) + 1
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.tf = [self._counts(d) for d in docs]

    @staticmethod
    def _counts(doc: List[str]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for t in doc:
            out[t] = out.get(t, 0) + 1
        return out

    def score(self, i: int, terms: List[str]) -> float:
        tf, dl, s = self.tf[i], len(self.docs[i]), 0.0
        for t in terms:
            f = tf.get(t)
            if f:
                s += self.idf.get(t, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
        return s


def _age_days(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)


def _money(monthly_usd: float, inr_per_usd: float, prefer_inr: bool) -> str:
    if prefer_inr:
        v = monthly_usd * inr_per_usd
        return f"₹{v / 1000:,.0f}k/mo" if v >= 1000 else f"₹{v:,.0f}/mo"
    return f"${monthly_usd / 1000:,.1f}k/mo" if monthly_usd >= 1000 else f"${monthly_usd:,.0f}/mo"


def rank(items: List[Dict[str, Any]], q: Query, inr_per_usd: float = 88.0, prefer_inr: bool = False,
         limit: int = 50, min_score: float = 0.0, trust: Optional[Dict[str, float]] = None,
         stats: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
    """
    Filter, score and de-duplicate items for a query. Returns copies with 'score' and 'reasons'.
    Locations are a hard filter; if that leaves fewer than 3 results they become a soft preference.
    If `stats` is given, it is filled with how many items each filter removed (see _rank).
    """
    q.trust = trust or SOURCE_PRIOR
    strict = _rank(items, q, inr_per_usd, prefer_inr, limit, min_score, strict_location=True, stats=stats)
    if len(strict) >= 3 or not q.locations:
        return strict
    named = [l for l in q.locations if l not in ("remote", "india")]
    if named and all(l in INDIA_WORDS for l in named):
        wider = Query(**{**q.__dict__, "locations": ["india", "remote"]})
        widened = _rank(items, wider, inr_per_usd, prefer_inr, limit, min_score, strict_location=True, stats=stats)
        if len(widened) > len(strict):
            q.note = f"Few matches in {', '.join(n.title() for n in named)}, so I widened to all of India and remote."
            return widened
    q.note = "Few matches in that location, so I included other places too."
    return _rank(items, q, inr_per_usd, prefer_inr, limit, min_score, strict_location=False, stats=stats)


def _rank(items, q, inr_per_usd, prefer_inr, limit, min_score, strict_location, stats=None):
    """
    `stats` (cleared on every call, so it describes the call whose results rank() returns) counts
    items removed by each filter: kind, not_remote, closed, too_old, unpaid, location, duplicate,
    low_score, and over_limit (good enough, but the list was full).
    """
    if stats is None:
        stats = {}
    stats.clear()
    drop = lambda reason: stats.__setitem__(reason, stats.get(reason, 0) + 1)
    now = datetime.now(timezone.utc)
    pool = []
    for it in items:
        if q.kinds and it.get("kind") not in q.kinds:
            drop("kind")
            continue
        if q.remote_only and not it.get("remote"):
            drop("not_remote")
            continue
        if it.get("deadline"):
            try:
                if datetime.fromisoformat(it["deadline"]) < now:
                    drop("closed")
                    continue
            except ValueError:
                pass
        age = _age_days(it.get("posted_at") or it.get("first_seen_at"))
        # Company boards and YC list only roles that are still open, so age lowers freshness but never hides them.
        live_board = it.get("source") in ("careers", "yc")
        if age is not None and age > q.max_age_days and it.get("status", "new") == "new" and not live_board:
            drop("too_old")
            continue
        if q.min_monthly_usd and it.get("pay_monthly_usd") == 0:
            drop("unpaid")
            continue
        pool.append((it, age))
    if not pool:
        return []

    docs = [_tokens(f"{it['title']} " * 3 + " ".join(it.get("skills") or []) * 2 + " " + (it.get("description") or "")[:4000])
            for it, _ in pool]
    bm25 = BM25(docs)
    terms = [t for term in q.terms for t in _tokens(term)]
    bm_scores = [bm25.score(i, terms) for i in range(len(pool))] if terms else [0.0] * len(pool)
    bm_max = max(bm_scores) or 1.0

    results = []
    for (it, age), bm in zip(pool, bm_scores):
        reasons: List[str] = []
        item_skills = set(it.get("skills") or [])
        title_lower = (it.get("title") or "").lower()

        title = it.get("title") or ""
        if q.skills:
            title_skills = set(extract_skills(title))
            strong = [sk for sk in q.skills if sk in title_skills or (sk in item_skills and it.get("source") == "unstop")]
            weak = [sk for sk in q.skills if sk in item_skills and sk not in strong]
            coverage = min(1.0, (len(strong) + 0.5 * len(weak)) / min(len(q.skills), 3))
            if strong or weak:
                reasons.append("Matches " + ", ".join((strong + weak)[:4]))
        else:
            coverage = 0.6
        if terms:
            coverage = 0.7 * coverage + 0.3 * (bm / bm_max)
        tech_query = bool(q.skills) or any(t in ("frontend", "backend", "fullstack", "full-stack", "web", "mobile", "ml", "ai", "devops", "cloud", "developer", "engineer", "software", "data") for t in q.terms)
        # Half of relevance is "is this the kind of role you do" (from the title), half is skill/keyword overlap.
        # Title-only listings (no description) still rank on their title instead of falling to zero.
        title_fit = 1.0 if (TECH_ROLE_RE.search(title) or not tech_query) else 0.0
        relevance = 0.5 * title_fit + 0.5 * coverage
        if OFF_ROLE_RE.search(title) and not re.search(r"software|developer|engineer|full[- ]?stack|web", title, re.I):
            relevance *= 0.2 if tech_query else 0.6
        elif not TECH_ROLE_RE.search(title):
            relevance *= 0.35 if tech_query else 0.8
        if SENIOR_RE.search(title) and (q.experience_years is None or q.experience_years < 4) and "job" not in q.kinds:
            relevance *= 0.5

        pay = it.get("pay_monthly_usd")
        if q.min_monthly_usd:
            if pay is None:
                pay_fit = 0.5
            else:
                ratio = pay / q.min_monthly_usd
                pay_fit = 1.0 if ratio >= 1 else ratio ** 2
                label = it.get("pay_text") or _money(pay, inr_per_usd, prefer_inr)
                reasons.append(f"Pays {label}" + (" (meets your ask)" if ratio >= 1 else " (below your ask)"))
        else:
            pay_fit = 0.6 if pay is None else 0.2 if pay == 0 else 0.8
            if pay and it.get("pay_text") != "Unpaid":
                reasons.append(f"Pays {it.get('pay_text') or _money(pay, inr_per_usd, prefer_inr)}")
            elif pay == 0:
                reasons.append("Unpaid")

        loc = (it.get("location") or "").lower()
        wanted = q.locations
        if not wanted:
            loc_fit = 1.0
        elif it.get("remote") and ("remote" in wanted or "india" in wanted) and remote_open_to(loc, wanted):
            loc_fit = 1.0
        elif "india" in wanted and any(re.search(rf"\b{w}\b", loc) for w in INDIA_WORDS):
            loc_fit = 1.0
        elif any(re.search(rf"\b{re.escape(w)}\b", loc) for w in wanted if w not in ("remote", "india")):
            loc_fit = 1.0
        else:
            loc_fit = 0.1
        if strict_location and loc_fit < 0.5:
            drop("location")
            continue
        if it.get("remote"):
            reasons.append("Remote")
        elif loc:
            reasons.append(it["location"][:40])

        if age is None:
            fresh = 0.4
        else:
            fresh = 0.5 ** (age / 10)
            reasons.append("Posted today" if age < 1 else f"Posted {age:.0f}d ago")

        need = it.get("experience_years")
        if need is None and SENIOR_RE.search(title):
            need = 5.0
        if q.experience_years is None or need is None:
            exp_fit = 0.7
        elif need <= q.experience_years + 0.25:
            exp_fit = 1.0
        else:
            exp_fit = max(0.0, 1 - (need - q.experience_years) / 2)
            reasons.append(f"Asks {need:g} yr experience")

        source_trust = (q.trust or SOURCE_PRIOR).get(it.get("source"), 0.7)
        if it.get("source") in ("careers", "yc"):
            reasons.insert(0, f"Direct from {it.get('company')}" + ("'s careers page" if it.get("source") == "careers" else " on YC"))
        # Relevance gates everything else: a fresh, well-paid role that isn't for you must not score well.
        fit = (0.15 * pay_fit + 0.15 * loc_fit + 0.15 * fresh + 0.10 * exp_fit) / 0.55
        base = (max(relevance, 0.0) ** 0.7) * (0.55 + 0.45 * fit)
        score = 10 * base * (0.7 + 0.3 * source_trust)
        results.append({**it, "score": round(score, 1), "reasons": reasons})

    results.sort(key=lambda r: r["score"], reverse=True)
    seen, unique = set(), []
    for r in results:
        key = r.get("dedupe_key") or f"{r.get('source')}:{r.get('id')}"
        if key in seen:
            drop("duplicate")
            continue
        seen.add(key)
        if r["score"] < min_score:
            drop("low_score")
        elif len(unique) >= limit:
            drop("over_limit")
        else:
            unique.append(r)
    return unique
