"""
Small JSON-backed state: user settings (auto-scan interval, enabled sources, email cap, saved alerts)
and the last run of every source. Lives in data/state.json (git-ignored).
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Any, Dict

STATE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "state.json"
_lock = threading.Lock()

DEFAULTS: Dict[str, Any] = {
    "settings": {
        "scan_interval_minutes": 0,      # 0 = auto-scan off
        "disabled_sources": ["unstop"],   # low reply rates reported; can be re-enabled in Settings
        "daily_email_cap": 20,
        "inr_per_usd": 88.0,
    },
    "alerts": [],                        # [{"id", "text", "created_at", "last_checked_at", "new_count"}]
    "runs": {},                          # source -> {"ok", "fetched", "new", "error", "finished_at"}
    "last_auto_scan_at": None,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state() -> Dict[str, Any]:
    with _lock:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
    merged = {**DEFAULTS, **data}
    merged["settings"] = {**DEFAULTS["settings"], **data.get("settings", {})}
    return merged


def save_state(state: Dict[str, Any]) -> None:
    with _lock:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(STATE_PATH)


def update_state(fn) -> Dict[str, Any]:
    """Read-modify-write helper: fn(state) mutates in place."""
    state = load_state()
    fn(state)
    save_state(state)
    return state


def record_run(source: str, ok: bool, fetched: int = 0, new: int = 0, error: str = None) -> None:
    update_state(lambda s: s["runs"].__setitem__(source, {
        "ok": ok, "fetched": fetched, "new": new, "error": error, "finished_at": now_iso(),
    }))
