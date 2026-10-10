"""
Hacker News source module for Freelance Lead Finder.
Uses the official public HN Algolia API (https://hn.algolia.com/api/v1/).
Completely read-only, no scraping, no browser automation.
"""

from datetime import datetime, timezone
import html
import logging
import re
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional
import requests
import yaml

logger = logging.getLogger("job_assistant.hn")


def load_leads_config() -> Dict[str, Any]:
    config_path = Path(__file__).resolve().parent.parent.parent / "config" / "leads.yaml"
    if not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def clean_html(raw_html: str) -> str:
    """Strip basic HTML tags from HN comment text and unescape HTML entities."""
    if not raw_html:
        return ""
    text = re.sub(r"<p>", "\n\n", raw_html)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


class HackerNewsSource:
    """
    Reads the monthly HN threads posted by the 'whoishiring' account:
    - "Ask HN: Who is hiring?" (company job posts)
    - "Ask HN: Freelancer? Seeking freelancer?" (only SEEKING FREELANCER comments)
    Posts are pre-ranked by remote + skill overlap so the LLM only sees the best few.
    """

    SEARCH_URL = "https://hn.algolia.com/api/v1/search"
    SEARCH_BY_DATE_URL = "https://hn.algolia.com/api/v1/search_by_date"
    WORK_TYPE_RE = re.compile(r"\b(contract|contractor|freelance|part[- ]time|intern|internship)\b", re.I)

    def __init__(self):
        self.config = load_leads_config()
        self.hn_cfg = self.config.get("hacker_news", {})
        self.blocked_keywords = [
            w.lower() for w in self.config.get("blocked_keywords", ["unpaid", "rev share"])
        ]
        self.last_error: Optional[str] = None

    def _latest_thread_id(self, title_prefix: str) -> Optional[str]:
        resp = requests.get(
            self.SEARCH_BY_DATE_URL,
            params={"query": title_prefix, "tags": "story,ask_hn", "hitsPerPage": 10},
            timeout=10,
        )
        resp.raise_for_status()
        for hit in resp.json().get("hits", []):
            if (hit.get("title") or "").startswith(title_prefix):
                return str(hit.get("objectID"))
        return None

    def _top_level_comments(self, story_id: str) -> List[Dict[str, Any]]:
        resp = requests.get(
            self.SEARCH_URL,
            params={"tags": f"comment,story_{story_id}", "hitsPerPage": 1000},
            timeout=15,
        )
        resp.raise_for_status()
        return [h for h in resp.json().get("hits", []) if str(h.get("parent_id")) == story_id]

    def _relevance(self, text_lower: str, skills: List[str]) -> int:
        score = sum(1 for s in skills if s and s in text_lower)
        if self.WORK_TYPE_RE.search(text_lower):
            score += 2
        return score

    def fetch_posts(self, skills: Optional[List[str]] = None, limit: int = 15) -> Generator[Dict[str, Any], None, None]:
        """Yield the `limit` most relevant remote posts from this month's HN hiring threads."""
        skills = [s.lower() for s in (skills or [])]
        candidates = []
        self.last_error = None

        threads = [
            ("Ask HN: Who is hiring?", None),
            ("Ask HN: Freelancer? Seeking freelancer?", "seeking freelancer"),
        ]
        for title_prefix, required_prefix in threads:
            try:
                story_id = self._latest_thread_id(title_prefix)
                if not story_id:
                    continue
                for hit in self._top_level_comments(story_id):
                    text = clean_html(hit.get("comment_text") or "")
                    text_lower = text.lower()
                    if len(text) < 80:
                        continue
                    if required_prefix and not text_lower.startswith(required_prefix):
                        continue
                    if "remote" not in text_lower:
                        continue
                    if any(blocked in text_lower for blocked in self.blocked_keywords):
                        continue
                    candidates.append((self._relevance(text_lower, skills), hit, text))
            except Exception as e:
                self.last_error = str(e)
                logger.error(f"Error reading HN thread '{title_prefix}': {e}")

        candidates.sort(key=lambda c: c[0], reverse=True)
        for _, hit, text in candidates[:limit]:
            item_id = str(hit.get("objectID"))
            yield {
                "source": "hn",
                "post_id": item_id,
                "title": headline_from_post(text),
                "content": text,
                "url": f"https://news.ycombinator.com/item?id={item_id}",
                "author": hit.get("author") or "anonymous",
                "created_utc": hit.get("created_at_i"),
            }


def headline_from_post(text: str) -> str:
    """HN hiring posts open with 'Company | Role | Location | ...'; use that first line as the title."""
    first_line = text.strip().split("\n")[0]
    parts = [p.strip() for p in first_line.split("|") if p.strip()]
    title = " · ".join(parts[:3]) if parts else first_line
    return title[:90]


def get_sample_hn_posts() -> List[Dict[str, Any]]:
    """Sample HN items for offline testing or demonstrations."""
    return [
        {
            "source": "hn",
            "post_id": "sample_hn_1",
            "title": "[HN] Ask HN: Freelancer? Seeking freelancer?",
            "content": "SEEKING FREELANCER | Remote | Looking for a Python/FastAPI engineer to build an async data ingestion pipeline for our analytics product. We pay $45-$60/hr. Expected engagement: 4 weeks part-time. Contact: jobs@metricpulse.dev with your GitHub.",
            "url": "https://news.ycombinator.com/item?id=sample_hn_1",
            "author": "mpulse_cto",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 5,
        },
        {
            "source": "hn",
            "post_id": "sample_hn_2",
            "title": "[HN] Ask HN: Freelancer? Seeking freelancer?",
            "content": "SEEKING FREELANCER | Next.js, Supabase, Tailwind | Building a b2b invoice portal. Need someone to take existing designs and implement responsive dashboard with Stripe invoicing. Budget $1,500-$2,000 fixed. Email founder@invoicerocket.io",
            "url": "https://news.ycombinator.com/item?id=sample_hn_2",
            "author": "invoicerocket",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 16,
        },
    ]
