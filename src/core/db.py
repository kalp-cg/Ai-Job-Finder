"""
Database module for Job + Freelance Assistant.
Uses SQLite to store leads, jobs, scores, draft replies, and track application lifecycle.
Enforces deduplication: never process the same URL or contact the same person twice.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
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

    conn.commit()
    conn.close()


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
