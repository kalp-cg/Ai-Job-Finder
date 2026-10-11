"""
Database module for Job + Freelance Assistant.
Uses SQLite to store leads, jobs, scores, draft replies, and track application lifecycle.
Enforces deduplication: never process the same URL or contact the same person twice.
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "assistant.db"


def get_db_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    target_path = db_path or DB_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    """Initialize database tables for leads and jobs with indexes."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    # Freelance Leads table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,                -- 'reddit', 'hn', etc.
            post_id TEXT,
            author TEXT,
            url TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            raw_content TEXT,
            need TEXT,
            budget TEXT,
            timeline TEXT,
            tech TEXT,                           -- JSON array of tech strings
            contact_method TEXT,
            score INTEGER DEFAULT 0,
            score_reason TEXT,
            draft_reply TEXT,
            status TEXT DEFAULT 'new',           -- 'new', 'contacted', 'replied', 'won', 'lost'
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Jobs / Internships table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,                -- 'greenhouse', 'lever', 'ashby', etc.
            company TEXT NOT NULL,
            title TEXT NOT NULL,
            location TEXT,
            url TEXT UNIQUE NOT NULL,
            stipend TEXT,
            requirements TEXT,
            score INTEGER DEFAULT 0,
            score_reason TEXT,
            draft_note TEXT,
            status TEXT DEFAULT 'new',           -- 'new', 'applied', 'interviewing', 'offer', 'rejected'
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Indexes for fast lookup & deduplication
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_url ON leads(url)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_author ON leads(author)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_url ON jobs(url)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")

    # Opportunities from public job/internship feeds (one row per source posting)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,                -- 'simplify', 'muse', 'unstop', ...
            external_id TEXT NOT NULL,
            dedupe_key TEXT,                     -- normalized company|title, for cross-source duplicates
            kind TEXT,                           -- 'internship', 'job', 'freelance'
            title TEXT NOT NULL,
            company TEXT,
            url TEXT,
            description TEXT,
            location TEXT,
            remote INTEGER DEFAULT 0,
            skills TEXT,                         -- JSON array of canonical skills
            pay_text TEXT,
            pay_monthly_usd REAL,
            experience_years REAL,
            contact_email TEXT,                  -- only set when the post asks for applications by email
            posted_at TEXT,
            deadline TEXT,
            active INTEGER DEFAULT 1,            -- 0 once the source stops listing it
            status TEXT DEFAULT 'new',           -- 'new', 'applied', 'interviewing', 'offer', 'rejected', 'passed'
            draft TEXT,
            notes TEXT,
            first_seen_at TEXT,
            last_seen_at TEXT,
            updated_at TEXT,
            UNIQUE(source, external_id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_opp_dedupe ON opportunities(dedupe_key)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_opp_status ON opportunities(status)")

    # Company careers boards SARTHI watches (one row per board)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            website TEXT,
            ats TEXT,                            -- 'greenhouse', 'lever', 'ashby', 'workable', 'smartrecruiters', 'recruitee', 'workday', 'yc', 'jsonld', or NULL if not found yet
            slug TEXT,                           -- board identifier on that ATS (or careers URL for 'jsonld' / 'workday')
            origin TEXT,                         -- 'yc', 'seed', 'user', 'hn'
            location_hint TEXT,
            discovery_state TEXT DEFAULT 'pending', -- 'pending', 'found', 'not_found'
            check_interval_hours REAL DEFAULT 24,
            next_check_at TEXT,
            last_checked_at TEXT,
            last_open_roles INTEGER,
            last_matches INTEGER,
            last_error TEXT,
            created_at TEXT,
            UNIQUE(name, ats, slug)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_companies_next ON companies(next_check_at)")

    # Every email SARTHI sends on the user's click
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS outreach (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_ref TEXT NOT NULL,              -- 'opp:12' or 'lead:7'
            to_email TEXT NOT NULL,
            subject TEXT,
            body TEXT,
            attachment TEXT,
            ok INTEGER NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL
        )
    """)

    # follow_up_at: when to nudge about something you applied to / contacted (added after launch).
    # Items already waiting on a reply get a reminder 5 days after they were last updated.
    for table, waiting in (("leads", "contacted"), ("opportunities", "applied")):
        cols = {r["name"] for r in cursor.execute(f"PRAGMA table_info({table})")}
        if "follow_up_at" not in cols:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN follow_up_at TEXT")
            rows = cursor.execute(f"SELECT id, updated_at FROM {table} WHERE status = ?", (waiting,)).fetchall()
            for row in rows:
                cursor.execute(f"UPDATE {table} SET follow_up_at = ? WHERE id = ?",
                               (follow_up_date(row["updated_at"]), row["id"]))

    conn.commit()
    conn.close()


FOLLOW_UP_DAYS = 5


def follow_up_date(from_iso: Optional[str] = None) -> str:
    """FOLLOW_UP_DAYS after from_iso (or now), as a UTC ISO timestamp."""
    start = datetime.now(timezone.utc)
    if from_iso:
        try:
            start = datetime.fromisoformat(from_iso.replace(" ", "T"))
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return (start + timedelta(days=FOLLOW_UP_DAYS)).isoformat()


def is_lead_seen(url: str, author: Optional[str] = None, db_path: Optional[Path] = None) -> bool:
    """
    Check if a lead URL has already been processed or if we have already
    contacted/seen this author.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    if author and author.strip().lower() not in {"[deleted]", "unknown", ""}:
        cursor.execute(
            "SELECT id FROM leads WHERE url = ? OR LOWER(author) = LOWER(?) LIMIT 1",
            (url.strip(), author.strip())
        )
    else:
        cursor.execute("SELECT id FROM leads WHERE url = ? LIMIT 1", (url.strip(),))

    row = cursor.fetchone()
    conn.close()
    return row is not None


def save_lead(
    source: str,
    title: str,
    url: str,
    raw_content: str,
    author: Optional[str] = None,
    post_id: Optional[str] = None,
    need: Optional[str] = None,
    budget: Optional[str] = None,
    timeline: Optional[str] = None,
    tech: Optional[List[str]] = None,
    contact_method: Optional[str] = None,
    score: int = 0,
    score_reason: Optional[str] = None,
    draft_reply: Optional[str] = None,
    status: str = "new",
    db_path: Optional[Path] = None,
) -> Optional[int]:
    """Save a scored freelance lead into the database. Skips duplicates gracefully."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    tech_json = json.dumps(tech or [])

    try:
        cursor.execute(
            """
            INSERT INTO leads (
                source, post_id, author, url, title, raw_content,
                need, budget, timeline, tech, contact_method,
                score, score_reason, draft_reply, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source, post_id, author, url.strip(), title, raw_content,
                need, budget, timeline, tech_json, contact_method,
                score, score_reason, draft_reply, status, now, now
            ),
        )
        conn.commit()
        lead_id = cursor.lastrowid
        return lead_id
    except sqlite3.IntegrityError:
        # Already exists
        return None
    finally:
        conn.close()


def get_top_leads(
    limit: int = 10,
    min_score: int = 7,
    status: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Retrieve top-scoring leads, sorted by score descending, then date descending."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    query = "SELECT * FROM leads WHERE score >= ?"
    params: List[Any] = [min_score]

    if status:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY score DESC, created_at DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    rows = cursor.fetchall()
    leads = [dict(row) for row in rows]
    conn.close()

    for lead in leads:
        if lead.get("tech"):
            try:
                lead["tech"] = json.loads(lead["tech"])
            except Exception:
                lead["tech"] = []
    return leads


def update_lead_status(
    lead_id: int,
    new_status: str,
    notes: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """Update lead status ('new', 'contacted', 'replied', 'won', 'lost')."""
    valid_statuses = {"new", "contacted", "replied", "won", "lost"}
    if new_status not in valid_statuses:
        raise ValueError(f"Invalid status '{new_status}'. Must be one of {valid_statuses}")

    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    if notes is not None:
        cursor.execute(
            "UPDATE leads SET status = ?, notes = ?, updated_at = ? WHERE id = ?",
            (new_status, notes, now, lead_id),
        )
    else:
        cursor.execute(
            "UPDATE leads SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, now, lead_id),
        )

    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def update_lead_draft(lead_id: int, draft: str, db_path: Optional[Path] = None) -> bool:
    """Save an edited draft reply for a lead."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute("UPDATE leads SET draft_reply = ?, updated_at = ? WHERE id = ?", (draft, now, lead_id))
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def update_lead(lead_id: int, db_path: Optional[Path] = None, **fields: Any) -> bool:
    """Update notes / follow_up_at on a lead."""
    allowed = {k: v for k, v in fields.items() if k in {"notes", "follow_up_at"}}
    if not allowed:
        return False
    allowed["updated_at"] = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection(db_path)
    cur = conn.execute(f"UPDATE leads SET {', '.join(f'{k} = ?' for k in allowed)} WHERE id = ?", (*allowed.values(), lead_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def get_lead(lead_id: int, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Fetch a single lead by id."""
    conn = get_db_connection(db_path)
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    conn.close()
    if not row:
        return None
    lead = dict(row)
    try:
        lead["tech"] = json.loads(lead.get("tech") or "[]")
    except Exception:
        lead["tech"] = []
    return lead


def is_job_seen(url: str, db_path: Optional[Path] = None) -> bool:
    """Check if a job URL has already been processed."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM jobs WHERE url = ? LIMIT 1", (url.strip(),))
    row = cursor.fetchone()
    conn.close()
    return row is not None


def save_job(
    source: str,
    company: str,
    title: str,
    url: str,
    location: Optional[str] = None,
    stipend: Optional[str] = None,
    requirements: Optional[str] = None,
    score: int = 0,
    score_reason: Optional[str] = None,
    draft_note: Optional[str] = None,
    status: str = "new",
    db_path: Optional[Path] = None,
) -> Optional[int]:
    """Save an internship or job posting into the database."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    try:
        cursor.execute(
            """
            INSERT INTO jobs (
                source, company, title, location, url, stipend,
                requirements, score, score_reason, draft_note, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source, company, title, location, url.strip(), stipend,
                requirements, score, score_reason, draft_note, status, now, now
            ),
        )
        conn.commit()
        job_id = cursor.lastrowid
        return job_id
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def get_top_jobs(
    limit: int = 10,
    min_score: int = 7,
    status: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Retrieve top-scoring jobs."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    query = "SELECT * FROM jobs WHERE score >= ?"
    params: List[Any] = [min_score]

    if status:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY score DESC, created_at DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    rows = cursor.fetchall()
    jobs = [dict(row) for row in rows]
    conn.close()
    return jobs


def update_job_status(
    job_id: int,
    new_status: str,
    notes: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """Update job status ('new', 'applied', 'interviewing', 'offer', 'rejected')."""
    valid_statuses = {"new", "applied", "interviewing", "offer", "rejected"}
    if new_status not in valid_statuses:
        raise ValueError(f"Invalid status '{new_status}'. Must be one of {valid_statuses}")

    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    if notes is not None:
        cursor.execute(
            "UPDATE jobs SET status = ?, notes = ?, updated_at = ? WHERE id = ?",
            (new_status, notes, now, job_id),
        )
    else:
        cursor.execute(
            "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, now, job_id),
        )

    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def get_summary_stats(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Get overview counts of leads and jobs across statuses."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT status, COUNT(*) as count FROM leads GROUP BY status")
    lead_counts = {row["status"]: row["count"] for row in cursor.fetchall()}

    cursor.execute("SELECT status, COUNT(*) as count FROM jobs GROUP BY status")
    job_counts = {row["status"]: row["count"] for row in cursor.fetchall()}

    conn.close()
    return {
        "leads": lead_counts,
        "jobs": job_counts,
    }


# ---------------------------------------------------------------- Opportunities

OPPORTUNITY_STATUSES = {"new", "applied", "interviewing", "offer", "rejected", "passed"}


def _opp_row(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    try:
        item["skills"] = json.loads(item.get("skills") or "[]")
    except Exception:
        item["skills"] = []
    return item


def upsert_opportunities(items: List[Dict[str, Any]], dedupe_key_fn, db_path: Optional[Path] = None) -> int:
    """Insert new postings and refresh existing ones. Returns how many were new."""
    conn = get_db_connection(db_path)
    now = datetime.now(timezone.utc).isoformat()
    new_count = 0
    for it in items:
        cur = conn.execute(
            """
            INSERT INTO opportunities (
                source, external_id, dedupe_key, kind, title, company, url, description, location, remote,
                skills, pay_text, pay_monthly_usd, experience_years, contact_email, posted_at, deadline,
                active, status, first_seen_at, last_seen_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'new', ?, ?, ?)
            ON CONFLICT(source, external_id) DO UPDATE SET
                title = excluded.title, company = excluded.company, url = excluded.url,
                description = excluded.description, location = excluded.location, remote = excluded.remote,
                skills = excluded.skills, pay_text = excluded.pay_text, pay_monthly_usd = excluded.pay_monthly_usd,
                experience_years = excluded.experience_years, contact_email = excluded.contact_email,
                deadline = excluded.deadline, active = 1, last_seen_at = excluded.last_seen_at,
                posted_at = COALESCE(opportunities.posted_at, excluded.posted_at)
            """,
            (
                it["source"], it["external_id"], dedupe_key_fn(it["company"], it["title"]), it["kind"], it["title"],
                it["company"], it["url"], it["description"], it["location"], int(it["remote"]),
                json.dumps(it["skills"]), it["pay_text"], it["pay_monthly_usd"], it["experience_years"],
                it["contact_email"], it["posted_at"], it["deadline"], now, now, now,
            ),
        )
        # INSERT ... ON CONFLICT reports rowcount 1 either way; a fresh row has first_seen_at == now
        if cur.rowcount and conn.execute(
            "SELECT first_seen_at FROM opportunities WHERE source = ? AND external_id = ?",
            (it["source"], it["external_id"]),
        ).fetchone()[0] == now:
            new_count += 1
    conn.commit()
    conn.close()
    return new_count


def mark_unlisted_inactive(source: str, seen_before: str, db_path: Optional[Path] = None) -> int:
    """After a full fetch, anything from `source` not seen since `seen_before` is no longer listed."""
    conn = get_db_connection(db_path)
    cur = conn.execute(
        "UPDATE opportunities SET active = 0 WHERE source = ? AND last_seen_at < ? AND active = 1",
        (source, seen_before),
    )
    conn.commit()
    conn.close()
    return cur.rowcount


def list_opportunities(active_only: bool = True, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    conn = get_db_connection(db_path)
    query = "SELECT * FROM opportunities" + (" WHERE active = 1 OR status != 'new'" if active_only else "")
    rows = conn.execute(query).fetchall()
    conn.close()
    return [_opp_row(r) for r in rows]


def get_opportunity(opp_id: int, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    conn = get_db_connection(db_path)
    row = conn.execute("SELECT * FROM opportunities WHERE id = ?", (opp_id,)).fetchone()
    conn.close()
    return _opp_row(row) if row else None


def update_opportunity(opp_id: int, db_path: Optional[Path] = None, **fields: Any) -> bool:
    """Update status / draft / notes / follow_up_at on an opportunity."""
    allowed = {k: v for k, v in fields.items() if k in {"status", "draft", "notes", "follow_up_at"}}
    if "status" in allowed and allowed["status"] not in OPPORTUNITY_STATUSES:
        raise ValueError(f"Invalid status '{allowed['status']}'. Must be one of {OPPORTUNITY_STATUSES}")
    if not allowed:
        return False
    allowed["updated_at"] = datetime.now(timezone.utc).isoformat()
    sets = ", ".join(f"{k} = ?" for k in allowed)
    conn = get_db_connection(db_path)
    cur = conn.execute(f"UPDATE opportunities SET {sets} WHERE id = ?", (*allowed.values(), opp_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


# ---------------------------------------------------------------- Outreach log

def record_outreach(item_ref: str, to_email: str, subject: str, body: str, attachment: Optional[str],
                    ok: bool, error: Optional[str] = None, db_path: Optional[Path] = None) -> None:
    conn = get_db_connection(db_path)
    conn.execute(
        "INSERT INTO outreach (item_ref, to_email, subject, body, attachment, ok, error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (item_ref, to_email, subject, body, attachment, int(ok), error, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def outreach_sent_since(since_iso: str, db_path: Optional[Path] = None) -> int:
    conn = get_db_connection(db_path)
    n = conn.execute("SELECT COUNT(*) FROM outreach WHERE ok = 1 AND created_at >= ?", (since_iso,)).fetchone()[0]
    conn.close()
    return n


def outreach_for(item_ref: str, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    conn = get_db_connection(db_path)
    rows = conn.execute("SELECT * FROM outreach WHERE item_ref = ? ORDER BY created_at DESC", (item_ref,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def outreach_by_ref(db_path: Optional[Path] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Every email attempt, grouped by item ref, newest first."""
    conn = get_db_connection(db_path)
    rows = conn.execute("SELECT * FROM outreach ORDER BY created_at DESC").fetchall()
    conn.close()
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        grouped.setdefault(r["item_ref"], []).append(dict(r))
    return grouped


# ---------------------------------------------------------------- Companies (careers boards)

def add_company(name: str, website: Optional[str] = None, origin: str = "user", location_hint: Optional[str] = None,
                ats: Optional[str] = None, slug: Optional[str] = None, db_path: Optional[Path] = None) -> Optional[int]:
    """Add a company to discover (ats=None) or a known board. Returns the row id, or None if it already exists."""
    conn = get_db_connection(db_path)
    now = datetime.now(timezone.utc).isoformat()
    if ats is None:
        # A discovery request is redundant if the company already has (or is finding) a non-YC board.
        exists = conn.execute(
            "SELECT id FROM companies WHERE LOWER(name) = LOWER(?) AND (ats IS NULL OR ats != 'yc')", (name,)
        ).fetchone()
    else:
        exists = conn.execute("SELECT id FROM companies WHERE ats = ? AND slug = ?", (ats, slug)).fetchone()
    if exists:
        conn.close()
        return None
    cur = conn.execute(
        """INSERT INTO companies (name, website, ats, slug, origin, location_hint, discovery_state, next_check_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, website, ats, slug, origin, location_hint, "found" if ats else "pending", now, now),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def list_companies(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    conn = get_db_connection(db_path)
    rows = conn.execute("SELECT * FROM companies ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def due_companies(now_iso: str, limit: int, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Boards whose next check is due, plus companies still waiting for discovery. Oldest first."""
    conn = get_db_connection(db_path)
    rows = conn.execute(
        """SELECT * FROM companies
           WHERE discovery_state != 'duplicate' AND (next_check_at IS NULL OR next_check_at <= ?)
           ORDER BY discovery_state = 'pending' DESC, next_check_at ASC LIMIT ?""",
        (now_iso, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_company(company_id: int, db_path: Optional[Path] = None, **fields: Any) -> None:
    allowed = {"website", "ats", "slug", "discovery_state", "check_interval_hours", "next_check_at", "last_checked_at",
               "last_open_roles", "last_matches", "last_error", "name"}
    data = {k: v for k, v in fields.items() if k in allowed}
    if not data:
        return
    conn = get_db_connection(db_path)
    conn.execute(f"UPDATE companies SET {', '.join(f'{k} = ?' for k in data)} WHERE id = ?", (*data.values(), company_id))
    conn.commit()
    conn.close()


def delete_company(company_id: int, db_path: Optional[Path] = None) -> bool:
    conn = get_db_connection(db_path)
    cur = conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0
