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
    ALGOLIA_URL = "https://hn.algolia.com/api/v1/search_by_date"

    def __init__(self):
        self.config = load_leads_config()
        self.hn_cfg = self.config.get("hacker_news", {})
        self.max_age_hours = self.config.get("max_lead_age_hours", 48)
        self.blocked_keywords = [
            w.lower() for w in self.config.get("blocked_keywords", ["unpaid", "rev share"])
        ]

    def fetch_posts(self, query: str = "SEEKING FREELANCER", limit: int = 30) -> Generator[Dict[str, Any], None, None]:
        """
        Fetch comments and stories from the official HN Algolia search API.
        Focuses on 'SEEKING FREELANCER' comments in monthly threads.
        """
        now_ts = int(datetime.now(timezone.utc).timestamp())
        min_created_ts = now_ts - (self.max_age_hours * 3600)

        params = {
            "query": query,
            "tags": "(story,comment)",
            "numericFilters": f"created_at_i>{min_created_ts}",
            "hitsPerPage": limit,
        }

        try:
            resp = requests.get(self.ALGOLIA_URL, params=params, timeout=10)
            if resp.status_code != 200:
                logger.error(f"HN Algolia API returned HTTP {resp.status_code}")
                return

            data = resp.json()
            hits = data.get("hits", [])

            for hit in hits:
                item_id = hit.get("objectID")
                author = hit.get("author") or "anonymous"
                story_title = hit.get("story_title") or hit.get("title") or "HN Lead"
                comment_text = hit.get("comment_text") or hit.get("story_text") or ""
                cleaned_text = clean_html(comment_text)

                if not cleaned_text or len(cleaned_text) < 30:
                    continue

                # Filter out candidates offering services rather than hiring
                text_lower = cleaned_text.lower()
                if "seeking work" in text_lower or "[for hire]" in text_lower:
                    continue

                if any(blocked in text_lower for blocked in self.blocked_keywords):
                    continue

                yield {
                    "source": "hn",
                    "post_id": str(item_id),
                    "title": f"[HN] {story_title[:70]}",
                    "content": cleaned_text,
                    "url": f"https://news.ycombinator.com/item?id={item_id}",
                    "author": author,
                    "created_utc": hit.get("created_at_i", now_ts),
                }

        except Exception as e:
            logger.error(f"Error querying HN Algolia API: {e}")


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
