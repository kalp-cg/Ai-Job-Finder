"""
Fetch every enabled source that is due, store results, mark postings that disappeared as closed,
and update saved alerts with their count of new matches.
"""

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.core.db import init_db, list_opportunities, mark_unlisted_inactive, upsert_opportunities
from src.opportunities.normalize import dedupe_key
from src.opportunities.ranking import parse_query, rank
from src.opportunities.sources import SOURCES
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
                record_run(key, False, error=str(e)[:300])
                report["sources"][key] = {"error": str(e)[:300]}
        report["finished_at"] = now_iso()
        update_alerts()
        return report
    finally:
        _running.release()


def update_alerts() -> None:
    """Count matches first seen since each alert was last opened."""
    state = load_state()
    if not state["alerts"]:
        return
    from src.web.agent import load_profile_data  # local import avoids a cycle at module load
    profile = load_profile_data()
    items = list_opportunities()
    rate = state["settings"]["inr_per_usd"]

    def apply(s: Dict[str, Any]) -> None:
        for alert in s["alerts"]:
            q = parse_query(alert["text"], profile, rate)
            matches = rank(items, q, rate, limit=200, min_score=6.0)
            since = alert.get("last_checked_at") or alert["created_at"]
            alert["new_count"] = sum(1 for m in matches if (m.get("first_seen_at") or "") > since)
            alert["total"] = len(matches)

    update_state(apply)
