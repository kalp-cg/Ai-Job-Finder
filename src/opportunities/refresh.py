"""
Fetch every enabled source that is due, store results, mark postings that disappeared as closed,
and update saved alerts with their count of new matches.
"""

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.core.db import get_top_leads, init_db, list_opportunities, mark_unlisted_inactive, upsert_opportunities
from src.opportunities.normalize import dedupe_key, detect_kind, extract_skills, is_remote
from src.opportunities.ranking import parse_query, rank
from src.opportunities.sources import SOURCES, plain_error
from src.opportunities.state import load_state, now_iso, record_run, update_state

logger = logging.getLogger("sarthi.refresh")

_running = threading.Lock()

# Sources whose fetch returns their complete open list, so missing items really are closed.
FULL_LIST_SOURCES = {"simplify"}
# Minimum gap for manual scans; sources with strict terms keep their own interval.
MANUAL_FLOOR_MINUTES = 10
STRICT_SOURCES = {"remotive"}


def _due(source: str, manual: bool, runs: Dict[str, Any]) -> bool:
    last = (runs.get(source) or {}).get("finished_at")
    if not last:
        return True
    gap = SOURCES[source]["min_interval_minutes"]
    if manual and source not in STRICT_SOURCES:
        gap = MANUAL_FLOOR_MINUTES
    return datetime.now(timezone.utc) - datetime.fromisoformat(last) >= timedelta(minutes=gap)


def is_running() -> bool:
    return _running.locked()


def refresh_sources(manual: bool = False, only: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """Returns a report, or None if a refresh is already running."""
    if not _running.acquire(blocking=False):
        return None
    try:
        init_db()
        state = load_state()
        disabled = set(state["settings"]["disabled_sources"])
        report = {"started_at": now_iso(), "sources": {}, "new": 0}
        for key in only or list(SOURCES):
            if key in disabled:
                continue
            if not _due(key, manual, state["runs"]):
                report["sources"][key] = {"skipped": "ran recently"}
                continue
            started = now_iso()
            try:
                items = [i for i in SOURCES[key]["fetch"](None) if i["title"] and i["url"]]
                new = upsert_opportunities(items, dedupe_key)
                if key in FULL_LIST_SOURCES and items:
                    mark_unlisted_inactive(key, started)
                record_run(key, True, fetched=len(items), new=new)
                report["sources"][key] = {"fetched": len(items), "new": new}
                report["new"] += new
                logger.info(f"{key}: {len(items)} fetched, {new} new")
            except Exception as e:
                logger.error(f"{key} failed: {e}")
                record_run(key, False, error=plain_error(e))
                report["sources"][key] = {"error": plain_error(e)}
        if "companies" not in disabled:
            from src.opportunities.companies import crawl_companies
            from src.web.agent import load_profile_data
            kinds = ((load_profile_data().get("search") or {}).get("kinds")) or ["internship"]
            try:
                crawl = crawl_companies(kinds, budget_seconds=150 if manual else 100, max_companies=80 if manual else 50)
                record_run("companies", True, fetched=crawl["checked"], new=crawl["new"])
                report["sources"]["companies"] = {"fetched": crawl["checked"], "new": crawl["new"], "crawl": crawl}
                report["new"] += crawl["new"]
            except Exception as e:
                logger.error(f"company crawl failed: {e}")
                record_run("companies", False, error=plain_error(e))
                report["sources"]["companies"] = {"error": plain_error(e)}
        report["finished_at"] = now_iso()
        update_alerts()
        return report
    finally:
        _running.release()


def lead_postings(min_score: int = 7) -> List[Dict[str, Any]]:
    """
    HN/Reddit leads shaped like feed postings, so rank() can score them next to feed postings.
    Uses the same score floor as the queue; sample leads are left out. 'lead_id' marks them.
    """
    out = []
    for l in get_top_leads(limit=1000, min_score=min_score):
        if "sample_" in (l.get("url") or ""):
            continue
        raw = l.get("raw_content") or ""
        out.append({
            "id": l["id"], "lead_id": l["id"], "source": l["source"], "company": "",
            "kind": detect_kind(l.get("title", ""), raw, default="job" if l["source"] == "hn" else "freelance"),
            "title": l.get("title") or "", "description": raw, "url": l.get("url"),
            "skills": extract_skills(l.get("title") or "", " ".join(l.get("tech") or []), raw),
            "location": "Remote" if is_remote(raw) else "", "remote": is_remote(raw),
            "pay_text": l.get("budget") or "", "pay_monthly_usd": None, "experience_years": None,
            "deadline": None, "posted_at": l.get("created_at"), "first_seen_at": l.get("created_at"),
            "status": l.get("status"), "dedupe_key": f"lead:{l['id']}",
        })
    return out


def update_alerts() -> None:
    """Count matches (feed postings and HN/Reddit leads) first seen since each alert was last opened."""
    state = load_state()
    if not state["alerts"]:
        return
    from src.web.agent import load_profile_data  # local import avoids a cycle at module load
    profile = load_profile_data()
    items = list_opportunities() + lead_postings()
    rate = state["settings"]["inr_per_usd"]

    def apply(s: Dict[str, Any]) -> None:
        for alert in s["alerts"]:
            q = parse_query(alert["text"], profile, rate)
            matches = rank(items, q, rate, limit=200, min_score=6.0)
            since = alert.get("last_checked_at") or alert["created_at"]
            alert["new_count"] = sum(1 for m in matches if (m.get("first_seen_at") or "") > since)
            alert["total"] = len(matches)

    update_state(apply)
