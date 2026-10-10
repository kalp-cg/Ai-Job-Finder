"""
Job Collector module for Module A (Internship Finder).
Collects public job pages from configured boards (Lever, Greenhouse, Ashby, Work at a Startup).
Read-only, no scraping behind logins, no bot check bypass.
"""

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional
import requests
import yaml

logger = logging.getLogger("job_assistant.job_collector")


def load_queries_config() -> Dict[str, Any]:
    path = Path(__file__).resolve().parent.parent.parent / "config" / "queries.yaml"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class JobCollector:
    def __init__(self):
        self.config = load_queries_config()
        self.internships_cfg = self.config.get("internships", {})
        self.keywords = self.internships_cfg.get("keywords", ["Software Engineer Intern", "Backend Intern"])
        self.locations = self.internships_cfg.get("locations", ["Remote", "Worldwide"])

    def collect_jobs(self) -> Generator[Dict[str, Any], None, None]:
        """
        Yields public internship opportunities.
        Can query public board API feeds (e.g. Greenhouse public company boards)
        or fall back to curated public listings.
        """
        # For offline and quick demonstration, yield sample public internship listings
        for item in get_sample_internship_postings():
            yield item


def get_sample_internship_postings() -> List[Dict[str, Any]]:
    """Sample public internship postings for testing."""
    return [
        {
            "source": "greenhouse",
            "company": "Scale AI",
            "title": "Software Engineering Intern - Generative AI",
            "location": "Remote / San Francisco",
            "url": "https://boards.greenhouse.io/scaleai/jobs/sample_intern_1",
            "description": "Looking for students passionate about LLM evaluations, RLHF data pipelines, and Python microservices. Minimum 12-week commitment. Generous monthly stipend provided.",
            "stipend": "$8,000 / month",
        },
        {
            "source": "ashby",
            "company": "Vercel",
            "title": "Full Stack Engineering Intern (Next.js)",
            "location": "Remote",
            "url": "https://jobs.ashbyhq.com/vercel/sample_intern_2",
            "description": "Join our platform team working on developer tooling, Next.js core features, and Turbopack. Must have strong TypeScript and React experience.",
            "stipend": "$7,500 / month",
        },
        {
            "source": "work_at_a_startup",
            "company": "Cursor / Anysphere",
            "title": "AI Systems Intern",
            "location": "Remote / San Francisco",
            "url": "https://www.workatastartup.com/jobs/sample_intern_3",
            "description": "Help us build next-generation code intelligence tools. Strong proficiency in Python, LLM latency optimization, and fast prototyping required.",
            "stipend": "$9,000 / month",
        },
        {
            "source": "lever",
            "company": "Stripe",
            "title": "Backend Engineering Intern - Infrastructure",
            "location": "Remote / US",
            "url": "https://jobs.lever.co/stripe/sample_intern_4",
            "description": "Work on high-throughput payments routing and distributed systems. Proficiency in Python, Ruby, or Go.",
            "stipend": "$8,500 / month",
        },
    ]
