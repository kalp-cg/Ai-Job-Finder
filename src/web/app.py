"""
FastAPI app for SARTHI: serves the web UI and its JSON API.

Items: one shape for the UI, covering HN/Reddit leads ('lead:<id>') and feed opportunities ('opp:<id>').
Auto-scan: a background loop runs refreshes on the interval chosen in Settings (off by default).
Email: sent only from POST /api/outreach/send, i.e. on the user's click. Never from the scheduler.
"""

import asyncio
import csv
import io
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.core.db import (
    delete_company,
    follow_up_date,
    get_lead,
    list_companies,
    get_opportunity,
    get_top_leads,
    init_db,
    list_opportunities,
    log_status,
    outreach_by_ref,
    outreach_for,
    status_history,
    update_lead,
    update_lead_draft,
    update_lead_status,
    update_opportunity,
)
from src.core.llm import get_llm_client
from src.opportunities import mailer
from src.opportunities.normalize import canonical_skill, detect_kind, find_email, is_remote
from src.opportunities.ranking import Query, learned_trust, parse_query, profile_query, rank
from src.opportunities.refresh import is_running, lead_postings, refresh_sources
from src.opportunities.sources import SOURCES
from src.opportunities.state import load_state, now_iso, update_state
from src.web.agent import (
    draft_application_note,
    draft_follow_up,
    handle_lead_message,
    interview_prep,
    handle_user_message,
    is_scan_request,
    load_profile_data,
    load_resume_text,
    onboard_user_profile,
    parse_resume_file,
)

logger = logging.getLogger("sarthi.web")

app = FastAPI(title="SARTHI", version="2.0.0")

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

LEAD_TAB = {"new": "new", "contacted": "sent", "replied": "sent", "won": "won", "lost": "passed"}
OPP_TAB = {"new": "new", "applied": "sent", "interviewing": "sent", "offer": "won", "rejected": "passed", "passed": "passed"}
SOURCE_LABELS = {"hn": "Hacker News", "reddit": "Reddit", "careers": "Company careers", "yc": "YC",
                 **{k: v["label"] for k, v in SOURCES.items()}}
QUEUE_SIZE = 60
QUEUE_MIN_SCORE = 5.0
# Statuses that mean "waiting on them": these get a follow-up reminder, every other status clears it.
WAITING_STATUSES = {"contacted", "applied"}


@app.middleware("http")
async def same_origin_only(request: Request, call_next):
    """This API can send email, so refuse state-changing requests that come from other websites."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and not re.match(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$", origin):
            return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
    return await call_next(request)


# ---------------------------------------------------------------- auto-scan loop

def run_scan(manual: bool) -> Optional[Dict[str, Any]]:
    """Refresh feeds, and HN/Reddit leads when they're due (those use LLM calls, so less often)."""
    report = refresh_sources(manual=manual)
    if report is None:
        return None
    runs = load_state()["runs"]
    last = (runs.get("hn") or {}).get("finished_at")
    gap_hours = 3 if manual else 12
    if not last or (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() > gap_hours * 3600:
        from main import process_leads
        try:
            report["new_leads"] = process_leads(use_sample=False, limit_per_source=8)
        except Exception as e:
            logger.error(f"Lead scan failed: {e}")
    return report


async def scheduler_loop():
    while True:
        await asyncio.sleep(30)
        try:
            state = load_state()
            interval = state["settings"]["scan_interval_minutes"]
            if not interval or is_running():
                continue
            last = state.get("last_auto_scan_at")
            if last and (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() < interval * 60:
                continue
            update_state(lambda s: s.__setitem__("last_auto_scan_at", now_iso()))
            logger.info("Auto-scan starting")
            await asyncio.to_thread(run_scan, False)
        except Exception as e:
            logger.error(f"Auto-scan error: {e}")


@app.on_event("startup")
async def on_startup():
    init_db()
    asyncio.create_task(scheduler_loop())


# ---------------------------------------------------------------- item shaping

def _prefer_inr(profile: Dict[str, Any]) -> bool:
    return (profile.get("search") or {}).get("currency") == "INR"


def lead_item(l: Dict[str, Any]) -> Dict[str, Any]:
    raw = l.get("raw_content") or ""
    return {
        "ref": f"lead:{l['id']}",
        "type": "lead",
        "kind": detect_kind(l.get("title", ""), raw, default="job" if l.get("source") == "hn" else "freelance"),
        "source": l.get("source"),
        "source_label": SOURCE_LABELS.get(l.get("source"), l.get("source")),
        "title": l.get("title"),
        "company": "",
        "url": l.get("url"),
        "location": "Remote" if is_remote(raw) else "",
        "remote": is_remote(raw),
        "pay_text": l.get("budget") if l.get("budget") and not re.match(r"(?i)none|unspecified|negotiable|n/a", l["budget"]) else "",
        "timeline": l.get("timeline"),
        "posted_at": l.get("created_at"),
        "deadline": None,
        "skills": l.get("tech") or [],
        "score": l.get("score"),
        "why": l.get("score_reason") or "",
        "reasons": [],
        "description": raw,
        "draft": l.get("draft_reply") or "",
        "status": l.get("status"),
        "tab": LEAD_TAB.get(l.get("status"), "new"),
        "notes": l.get("notes") or "",
        "follow_up_at": l.get("follow_up_at"),
        "outreach": [],
        "contact_email": find_email(raw),
        "contact_hint": l.get("contact_method"),
        "is_sample": "sample_" in (l.get("url") or ""),
    }


def opp_item(o: Dict[str, Any]) -> Dict[str, Any]:
    reasons = o.get("reasons") or []
    return {
        "ref": f"opp:{o['id']}",
        "type": "opp",
        "kind": o.get("kind"),
        "source": o.get("source"),
        "source_label": (f"{o.get('company')} careers" if o.get("source") == "careers"
                         else f"{o.get('company')} on YC" if o.get("source") == "yc"
                         else SOURCE_LABELS.get(o.get("source"), o.get("source"))),
        "title": o.get("title"),
        "company": o.get("company"),
        "url": o.get("url"),
        "location": o.get("location"),
        "remote": bool(o.get("remote")),
        "pay_text": o.get("pay_text") or "",
        "timeline": None,
        "posted_at": o.get("posted_at") or o.get("first_seen_at"),
        "deadline": o.get("deadline"),
        "experience_years": o.get("experience_years"),
        "skills": o.get("skills") or [],
        "score": o.get("score"),
        "why": " · ".join(reasons),
        "reasons": reasons,
        "description": o.get("description") or "",
        "draft": o.get("draft") or "",
        "status": o.get("status"),
        "tab": OPP_TAB.get(o.get("status"), "new"),
        "notes": o.get("notes") or "",
        "follow_up_at": o.get("follow_up_at"),
        "outreach": [],
        "contact_email": o.get("contact_email"),
        "contact_hint": None,
        "active": bool(o.get("active", 1)),
        "is_sample": False,
    }


def watched_companies() -> set:
    """Normalized names of companies with a careers board (or YC page) SARTHI already checks."""
    from src.opportunities.companies import _norm
    return {_norm(c["name"]) for c in list_companies() if c["ats"] and c["discovery_state"] != "duplicate"}


def mark_watching(items: List[Dict[str, Any]]) -> None:
    from src.opportunities.companies import _norm
    watched = watched_companies()
    for it in items:
        it["watching"] = bool(it.get("company")) and _norm(it["company"]) in watched


def mark_skill_gap(items: List[Dict[str, Any]]) -> None:
    """missing_skills: known tech skills the post asks for that aren't in your profile."""
    # Every profile skill, not profile_query's top 12 (that cap is for ranking only).
    skills_cfg = load_profile_data().get("skills") or {}
    listed = [s for group in skills_cfg.values() for s in (group or [])] if isinstance(skills_cfg, dict) else list(skills_cfg)
    mine = {(canonical_skill(s) or s).lower() for s in listed}
    for it in items:
        gap = []
        for raw in it.get("skills") or []:
            skill = canonical_skill(raw)
            if skill and skill.lower() not in mine and skill not in gap:
                gap.append(skill)
        it["missing_skills"] = gap[:6]


def current_trust(opps: List[Dict[str, Any]]) -> Dict[str, float]:
    """Source trust learned from your own outcomes on feed postings and HN/Reddit leads."""
    leads = [{"source": l["source"], "status": l["status"]} for l in get_top_leads(limit=1000, min_score=0) if "sample_" not in (l.get("url") or "")]
    return learned_trust(opps + leads)


def get_item(ref: str) -> Dict[str, Any]:
    kind, _, raw_id = ref.partition(":")
    if not raw_id.isdigit():
        raise HTTPException(status_code=400, detail="Bad item reference")
    if kind == "lead":
        lead = get_lead(int(raw_id))
        if lead:
            return lead_item(lead)
    elif kind == "opp":
        opp = get_opportunity(int(raw_id))
        if opp:
            profile = load_profile_data()
            q = profile_query(profile, load_state()["settings"]["inr_per_usd"])
            scored = rank([opp], Query(skills=q.skills, min_monthly_usd=q.min_monthly_usd, experience_years=q.experience_years, max_age_days=100000),
                          prefer_inr=_prefer_inr(profile), limit=1)
            return opp_item(scored[0] if scored else opp)
    raise HTTPException(status_code=404, detail="Item not found")


# ---------------------------------------------------------------- pages & profile

class ChatRequest(BaseModel):
    message: str
    history: Optional[List[Dict[str, str]]] = []
    item_ref: Optional[str] = None


class DraftRequest(BaseModel):
    draft: str


class StatusRequest(BaseModel):
    status: str


class NotesRequest(BaseModel):
    notes: str


class FollowUpRequest(BaseModel):
    follow_up_at: Optional[str] = None  # ISO timestamp, or null to clear


class OnboardRequest(BaseModel):
    name: str
    skills: str
    github_url: str
    linkedin_url: Optional[str] = ""
    resume_or_bio: str
    hourly_rate: Optional[int] = 35
    min_budget: Optional[int] = 250


class SearchPrefs(BaseModel):
    kinds: List[str]
    locations: List[str]
    remote_only: bool = False
    experience_years: Optional[float] = None
    expected_monthly: Optional[float] = None
    currency: str = "INR"


class SettingsRequest(BaseModel):
    scan_interval_minutes: Optional[int] = None
    disabled_sources: Optional[List[str]] = None
    daily_email_cap: Optional[int] = None


class AlertRequest(BaseModel):
    text: str


class SendRequest(BaseModel):
    ref: str
    to: str
    subject: str
    body: str
    attach_resume: bool = True


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/profile")
def get_profile():
    return {"profile": load_profile_data(), "has_resume": bool(load_resume_text())}


@app.post("/api/profile/upload-resume")
async def upload_resume(file: UploadFile = File(...)):
    return parse_resume_file(await file.read(), file.filename or "resume.pdf")


@app.post("/api/onboard")
def onboard_profile(req: OnboardRequest):
    return onboard_user_profile(
        name=req.name,
        skills_input=req.skills,
        github_url=req.github_url,
        linkedin_url=req.linkedin_url or "",
        resume_or_bio=req.resume_or_bio,
        hourly_rate=req.hourly_rate or 35,
        min_budget=req.min_budget or 250,
    )


@app.put("/api/profile/search")
def save_search_prefs(req: SearchPrefs):
    import yaml
    from src.web.agent import CONFIG_DIR
    profile = load_profile_data()
    profile["search"] = req.model_dump()
    (CONFIG_DIR / "profile.yaml").write_text(yaml.dump(profile, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {"status": "success"}


# ---------------------------------------------------------------- items

@app.get("/api/items")
def list_items():
    """Everything the queue shows: HN/Reddit leads plus the best-matching feed postings for the profile."""
    state = load_state()
    rate = state["settings"]["inr_per_usd"]
    profile = load_profile_data()
    prefer_inr = _prefer_inr(profile)

    items = [lead_item(l) for l in get_top_leads(limit=200, min_score=7)]

    disabled = set(state["settings"]["disabled_sources"])
    opps = list_opportunities()
    open_new = [o for o in opps if o["status"] == "new" and o["active"]]
    fresh = [o for o in open_new if o["source"] not in disabled]
    acted = [o for o in opps if o["status"] != "new"]
    pq = profile_query(profile, rate)
    trust = current_trust(opps)
    filtered: Dict[str, int] = {}
    ranked = rank(fresh, pq, rate, prefer_inr, limit=QUEUE_SIZE, min_score=QUEUE_MIN_SCORE, trust=trust, stats=filtered)
    items += [opp_item(o) for o in ranked]
    items += [opp_item(o) for o in acted]  # already acted on: keep regardless of filters
    sent = outreach_by_ref()
    for it in items:
        it["outreach"] = sent.get(it["ref"], [])
    mark_watching(items)
    mark_skill_gap(items)
    # Why the rest of the open postings aren't in the queue, so the briefing can say it plainly.
    queue_stats = {"open": len(open_new), "shown": len(ranked), "limit": QUEUE_SIZE, "min_score": QUEUE_MIN_SCORE,
                   "disabled_sources": len(open_new) - len(fresh), **filtered}
    return {"items": items, "profile_query": pq.chips(), "total_postings": len(opps), "queue_stats": queue_stats}


@app.get("/api/items/{ref}")
def item_detail(ref: str):
    item = get_item(ref)
    item["outreach"] = outreach_for(ref)
    item["timeline"] = status_history(ref)
    mark_watching([item])
    mark_skill_gap([item])
    return item


def change_status(ref: str, status: str) -> bool:
    """Set an item's status. Waiting statuses get a follow-up reminder; any other status clears it."""
    kind, _, raw_id = ref.partition(":")
    follow_up_at = follow_up_date() if status in WAITING_STATUSES else None
    if kind == "lead":
        ok = update_lead_status(int(raw_id), status) and update_lead(int(raw_id), follow_up_at=follow_up_at)
    else:
        ok = update_opportunity(int(raw_id), status=status, follow_up_at=follow_up_at)
    if ok:
        log_status(ref, status)
    return ok


@app.post("/api/items/{ref}/status")
def set_item_status(ref: str, req: StatusRequest):
    try:
        ok = change_status(ref, req.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"status": "success"}


@app.put("/api/items/{ref}/notes")
def save_item_notes(ref: str, req: NotesRequest):
    kind, _, raw_id = ref.partition(":")
    notes = req.notes.strip()
    ok = update_lead(int(raw_id), notes=notes) if kind == "lead" else update_opportunity(int(raw_id), notes=notes)
    if not ok:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"status": "success"}


@app.put("/api/items/{ref}/follow-up")
def save_item_follow_up(ref: str, req: FollowUpRequest):
    when = None
    if req.follow_up_at:
        try:
            parsed = datetime.fromisoformat(req.follow_up_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Follow-up date must be an ISO date")
        when = (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()
    kind, _, raw_id = ref.partition(":")
    ok = update_lead(int(raw_id), follow_up_at=when) if kind == "lead" else update_opportunity(int(raw_id), follow_up_at=when)
    if not ok:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"follow_up_at": when}


@app.put("/api/items/{ref}/draft")
def save_item_draft(ref: str, req: DraftRequest):
    kind, _, raw_id = ref.partition(":")
    ok = update_lead_draft(int(raw_id), req.draft.strip()) if kind == "lead" else update_opportunity(int(raw_id), draft=req.draft.strip())
    if not ok:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"status": "success"}


@app.post("/api/items/{ref}/write-draft")
def write_item_draft(ref: str):
    """Write an application note for a feed posting (Gemini if available, else a plain template)."""
    item = get_item(ref)
    if item["type"] != "opp":
        raise HTTPException(status_code=400, detail="Leads already have a draft")
    draft = draft_application_note(item)
    update_opportunity(int(ref.split(":")[1]), draft=draft)
    return {"draft": draft}


@app.post("/api/items/{ref}/follow-up-draft")
def write_follow_up(ref: str):
    """A follow-up message for something you applied to. Shown for you to edit and send yourself."""
    item = get_item(ref)
    sent = [o for o in outreach_for(ref) if o["ok"]]
    applied = [t for t in status_history(ref) if t["status"] in WAITING_STATUSES]
    when = sent[0]["created_at"] if sent else (applied[-1]["created_at"] if applied else None)
    return {"text": draft_follow_up(item, when)}


@app.post("/api/items/{ref}/interview-prep")
def write_interview_prep(ref: str):
    return {"text": interview_prep(get_item(ref))}


def _cell(value: Any) -> Any:
    """Post text comes from strangers: stop spreadsheets from running a cell like '=HYPERLINK(...)' as a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value if value is not None else ""


@app.get("/api/export.csv")
def export_csv():
    """Everything you've acted on (sent, won, passed), with notes and email counts, as a spreadsheet."""
    sent = outreach_by_ref()
    rows = [lead_item(l) for l in get_top_leads(limit=100000, min_score=0) if l["status"] != "new"]
    rows += [opp_item(o) for o in list_opportunities(active_only=False) if o["status"] != "new"]
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow(["type", "title", "company", "source", "status", "pay", "location", "url",
                  "follow_up_at", "emails_sent", "last_email_at", "notes"])
    for it in rows:
        if it["is_sample"]:
            continue
        mails = [o for o in sent.get(it["ref"], []) if o["ok"]]
        out.writerow([_cell(v) for v in (it["kind"], it["title"], it["company"], it["source_label"], it["status"],
                      it["pay_text"], it["location"], it["url"], it["follow_up_at"] or "", len(mails),
                      mails[0]["created_at"] if mails else "", it["notes"])])
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="sarthi-applications-{stamp}.csv"'})


# ---------------------------------------------------------------- chat & search

SEARCH_INTENT = re.compile(r"\b(need|find|search|show|looking|want|get|any|list|suggest|recommend|internships?|jobs?|roles?|openings?|positions?|freelance|gigs?)\b", re.I)


def search_items(text: str, limit: int = 8) -> Dict[str, Any]:
    state = load_state()
    rate = state["settings"]["inr_per_usd"]
    profile = load_profile_data()
    q = parse_query(text, profile, rate)
    disabled = set(state["settings"]["disabled_sources"])
    opps = list_opportunities()
    pool = [o for o in opps if o["active"] and o["source"] not in disabled] + lead_postings()
    results = rank(pool, q, rate, _prefer_inr(profile), limit=limit, min_score=5.0, trust=current_trust(opps))
    return {"query": q, "results": [search_result_item(r) for r in results]}


def search_result_item(r: Dict[str, Any]) -> Dict[str, Any]:
    """A ranked search hit in the UI's item shape. Leads keep their own write-up but take the search score."""
    if not r.get("lead_id"):
        return opp_item(r)
    item = lead_item(get_lead(r["lead_id"]))
    item["score"] = r["score"]
    return item


@app.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    if req.item_ref:
        # An explicit "track example.com" still works inside a post's thread (SARTHI suggests it there).
        target = parse_track_request(message) if TRACK_RE.match(message) else None
        if target:
            return track_reply(*target)
        item = get_item(req.item_ref)
        return handle_lead_message(message, item, req.history)

    if is_scan_request(message.lower()):
        return {"reply": None, "action": "scan"}

    target = parse_track_request(message)
    if target:
        return track_reply(*target)

    q = parse_query(message, load_profile_data())
    if (q.kinds or q.skills) and SEARCH_INTENT.search(message) and len(message) < 400:
        found = search_items(message)
        results, q = found["results"], found["query"]
        if results:
            reply = f"Here {'is' if len(results) == 1 else 'are'} the best {len(results)} I have right now, ranked for what you asked."
        else:
            reply = "Nothing I've collected matches that yet. Run a scan, or loosen one of the filters (pay or location usually)."
        return {"reply": reply, "note": q.note, "chips": q.chips(), "results": results, "search_text": message}

    return {"reply": handle_user_message(message, req.history)}


# ---------------------------------------------------------------- companies

TRACK_RE = re.compile(r"^\s*(?:please\s+)?(?:track|watch|follow|add company|monitor)\s+(?:company\s+)?(.+?)\s*$", re.I | re.S)
URL_RE = re.compile(r"https?://[^\s)>\]]+")


def parse_track_request(message: str):
    """
    ('Company', website or None) when the user asks to watch a company or pastes a hiring post
    (e.g. from LinkedIn). SARTHI never opens LinkedIn itself; it only reads what you paste.
    """
    urls = URL_RE.findall(message)
    linkedin = [u for u in urls if "linkedin.com" in u]
    others = [u for u in urls if "linkedin.com" not in u]
    m = TRACK_RE.match(message)
    if m and len(m.group(1)) < 80:
        target = m.group(1).strip().rstrip(".")
        if re.fullmatch(r"(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?:/\S*)?", target, re.I):
            target = f"https://{target}"  # a bare domain like "example.com" is a website, not a name
        if URL_RE.match(target):
            host = re.sub(r"^www\.", "", target.split("/")[2])
            return (host.split(".")[0].capitalize(), target)
        return (target, others[0] if others else None)

    is_post = bool(linkedin) or (len(message) > 120 and re.search(r"\bhiring\b|\bintern(ship)?s?\b|\bopenings?\b|\bjoin (our|us)\b", message, re.I))
    if not is_post:
        return None
    company = None
    for u in linkedin:
        mm = re.search(r"linkedin\.com/company/([\w-]+)", u)
        if mm:
            company = mm.group(1).replace("-", " ").title()
    if not company:
        company = extract_company_name(message)
    if not company:
        for u in linkedin:
            mm = re.search(r"linkedin\.com/posts/([a-z0-9-]+)_", u)
            if mm:
                company = mm.group(1).replace("-", " ").title()
    if not company:
        return None
    return (company, others[0] if others else None)


def extract_company_name(text: str) -> Optional[str]:
    llm = get_llm_client()
    if llm.is_available:
        raw = llm.generate_text(
            f"Which company is hiring in this post? Reply with only the company name, or NONE.\n\n{text[:3000]}",
            system_instruction="Reply with just the company name or NONE.",
        )
        name = (raw or "").strip().strip('"').strip()
        if name and name.upper() != "NONE" and len(name) < 60:
            return name
    for rx in (r"\b([A-Z][\w&.\-]*(?:\s[A-Z][\w&.\-]*){0,2}) is hiring", r"\b(?:at|join)\s+([A-Z][\w&.\-]*(?:\s[A-Z][\w&.\-]*){0,2})"):
        mm = re.search(rx, text)
        if mm:
            return mm.group(1)
    return None


def track_reply(name: str, website: Optional[str]) -> Dict[str, Any]:
    from src.opportunities.companies import track_company
    kinds = ((load_profile_data().get("search") or {}).get("kinds")) or ["internship", "job"]
    result = track_company(name, website, kinds)
    boards = result["boards"]
    if boards:
        where = " and ".join(f"their {b['label']} board" if b["ats"] not in ("yc", "jsonld", "html") else ("their YC page" if b["ats"] == "yc" else "their careers page") for b in boards)
        reply = (f"Watching **{result['name']}**. I found {where}: {result['open_roles']} open roles, "
                 f"{result['matches']} fit what you're looking for.")
        reply += " I'll check them again on every scan." if result["matches"] else " I'll tell you when a role that fits shows up."
    elif result.get("website"):
        reply = (f"I found {result['name']}'s website ({result['website']}) but no public careers page or hiring system yet. "
                 "Some companies only post on LinkedIn. I'll check their site again every week.")
    else:
        reply = (f"I couldn't find a website or careers board for {result['name']}. "
                 "Send me their website, like \"track example.com\", and I'll look there.")
    from src.opportunities.companies import _norm
    opps = [o for o in list_opportunities() if _norm(o["company"]) == _norm(result["name"]) and o["active"] and o["status"] == "new"]
    profile = load_profile_data()
    ranked = rank(opps, Query(skills=profile_query(profile).skills, max_age_days=100000), prefer_inr=_prefer_inr(profile), limit=8)
    return {"reply": reply, "results": [opp_item(o) for o in ranked]}


class CompanyRequest(BaseModel):
    name: str
    website: Optional[str] = None


@app.get("/api/companies")
def get_companies():
    rows = [r for r in list_companies() if r["discovery_state"] != "duplicate"]
    boards = [r for r in rows if r["ats"]]
    return {
        "total": len({r["name"].lower() for r in rows}),
        "boards": len(boards),
        "pending": sum(1 for r in rows if r["discovery_state"] == "pending"),
        "not_found": len({r["name"].lower() for r in rows if r["discovery_state"] == "not_found"} - {r["name"].lower() for r in boards}),
        "with_matches": sum(1 for r in boards if r.get("last_matches")),
        "companies": sorted(rows, key=lambda r: (-(r.get("last_matches") or 0), r["origin"] != "user", r["name"].lower())),
    }


@app.post("/api/companies")
def post_company(req: CompanyRequest):
    return track_reply(req.name.strip(), (req.website or "").strip() or None)


@app.delete("/api/companies/{company_id}")
def remove_company(company_id: int):
    if not delete_company(company_id):
        raise HTTPException(status_code=404, detail="Company not found")
    return {"status": "success"}


# ---------------------------------------------------------------- alerts

@app.get("/api/alerts")
def get_alerts():
    return {"alerts": load_state()["alerts"]}


@app.post("/api/alerts")
def add_alert(req: AlertRequest):
    alert = {"id": uuid.uuid4().hex[:8], "text": req.text.strip(), "created_at": now_iso(),
             "last_checked_at": now_iso(), "new_count": 0}
    update_state(lambda s: s["alerts"].append(alert))
    return alert


@app.post("/api/alerts/{alert_id}/seen")
def alert_seen(alert_id: str):
    def apply(s):
        for a in s["alerts"]:
            if a["id"] == alert_id:
                a["last_checked_at"], a["new_count"] = now_iso(), 0
    update_state(apply)
    return {"status": "success"}


@app.delete("/api/alerts/{alert_id}")
def delete_alert(alert_id: str):
    update_state(lambda s: s.__setitem__("alerts", [a for a in s["alerts"] if a["id"] != alert_id]))
    return {"status": "success"}


# ---------------------------------------------------------------- scan, status, settings

@app.post("/api/scan")
def scan_now():
    report = run_scan(manual=True)
    if report is None:
        raise HTTPException(status_code=409, detail="A scan is already running")
    return report


@app.get("/api/status")
def system_status():
    state = load_state()
    return {
        "gemini": get_llm_client().is_available,
        "email": mailer.is_configured(),
        "resume_file": (mailer.resume_file().name if mailer.resume_file() else None),
        "scanning": is_running(),
        "settings": state["settings"],
        "runs": state["runs"],
        "alerts": state["alerts"],
        "last_auto_scan_at": state.get("last_auto_scan_at"),
        "sources": [{"key": k, "label": v["label"], "about": v["about"]} for k, v in SOURCES.items()],
        "companies": {"boards": sum(1 for c in list_companies() if c["ats"])},
    }


@app.put("/api/settings")
def save_settings(req: SettingsRequest):
    changes = {k: v for k, v in req.model_dump().items() if v is not None}
    if "scan_interval_minutes" in changes and changes["scan_interval_minutes"] not in (0, 15, 30, 60, 180, 360, 720):
        raise HTTPException(status_code=400, detail="Unsupported interval")
    if "daily_email_cap" in changes:
        changes["daily_email_cap"] = max(1, min(50, changes["daily_email_cap"]))
    update_state(lambda s: s["settings"].update(changes))
    return load_state()["settings"]


# ---------------------------------------------------------------- email

@app.post("/api/outreach/send")
def send_application(req: SendRequest):
    item = get_item(req.ref)
    result = mailer.send_email(
        item_ref=req.ref,
        to=req.to,
        subject=req.subject,
        body=req.body,
        attach_resume=req.attach_resume,
        daily_cap=load_state()["settings"]["daily_email_cap"],
    )
    if result["ok"]:
        kind, _, raw_id = req.ref.partition(":")
        if kind == "lead":
            update_lead_draft(int(raw_id), req.body)
        else:
            update_opportunity(int(raw_id), draft=req.body)
        if item["status"] == "new":
            change_status(req.ref, "contacted" if kind == "lead" else "applied")
    return result


@app.post("/api/outreach/test")
def send_test():
    return mailer.send_test_email()
