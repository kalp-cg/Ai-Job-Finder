"""
Reddit source module for Freelance Lead Finder.
Uses official PRAW client in read-only mode.
Never posts, comments, or votes.
"""

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional
import yaml

logger = logging.getLogger("job_assistant.reddit")


def load_leads_config() -> Dict[str, Any]:
    config_path = Path(__file__).resolve().parent.parent.parent / "config" / "leads.yaml"
    if not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class RedditSource:
    def __init__(self):
        self.config = load_leads_config()
        self.reddit_cfg = self.config.get("reddit", {})
        self.subreddits = self.reddit_cfg.get("subreddits", ["forhire", "freelance_forhire", "webdev"])
        self.max_age_hours = self.config.get("max_lead_age_hours", 48)
        self.blocked_keywords = [
            w.lower() for w in self.config.get("blocked_keywords", ["unpaid", "for hire", "rev share"])
        ]
        self.flair_blocklist = [
            f.lower() for f in self.reddit_cfg.get("flair_blocklist", ["for hire", "[for hire]"])
        ]

        self.client_id = os.getenv("REDDIT_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
        self.user_agent = os.getenv("REDDIT_USER_AGENT", "JobAssistantBot/1.0 by local_user").strip()

        self._reddit = None
        if self.client_id and self.client_secret:
            try:
                import praw
                self._reddit = praw.Reddit(
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                    user_agent=self.user_agent,
                    check_for_async=False,
                )
                self._reddit.read_only = True
                logger.info("Reddit PRAW client initialized in read-only mode.")
            except Exception as e:
                logger.error(f"Failed to initialize PRAW: {e}")
        else:
            logger.info("Reddit API credentials not provided in .env (read-only live fetch requires client id/secret).")

    @property
    def is_configured(self) -> bool:
        return self._reddit is not None

    def fetch_posts(self, limit_per_sub: int = 25) -> Generator[Dict[str, Any], None, None]:
        """Fetch candidate posts from configured subreddits within the last 48 hours."""
        if not self.is_configured:
            logger.warning("Reddit API credentials not configured. Use sample posts or supply keys in .env.")
            return

        now_ts = datetime.now(timezone.utc).timestamp()
        max_age_secs = self.max_age_hours * 3600

        for sub_name in self.subreddits:
            try:
                subreddit = self._reddit.subreddit(sub_name)
                # Read new posts
                for post in subreddit.new(limit=limit_per_sub):
                    # Check age
                    post_age_secs = now_ts - post.created_utc
                    if post_age_secs > max_age_secs:
                        continue

                    # Filter by link flair
                    flair = (post.link_flair_text or "").lower()
                    if any(blocked in flair for blocked in self.flair_blocklist):
                        continue

                    # Filter by title keywords (e.g. "[For Hire]")
                    title_lower = post.title.lower()
                    if "[for hire]" in title_lower or "for hire" in title_lower:
                        continue

                    # Check blocked keywords in title + selftext
                    combined_text = f"{title_lower}\n{post.selftext.lower()}"
                    if any(blocked in combined_text for blocked in self.blocked_keywords):
                        continue

                    yield {
                        "source": "reddit",
                        "post_id": post.id,
                        "subreddit": sub_name,
                        "title": post.title,
                        "content": post.selftext,
                        "url": f"https://reddit.com{post.permalink}",
                        "author": str(post.author) if post.author else "anonymous",
                        "created_utc": post.created_utc,
                    }

            except Exception as e:
                logger.error(f"Error fetching posts from r/{sub_name}: {e}")
                continue


def get_sample_reddit_posts() -> List[Dict[str, Any]]:
    """
    Returns 20 realistic sample posts for testing, development, and offline evaluation
    as specified in build order step 1.
    """
    return [
        {
            "source": "reddit",
            "post_id": "sample_r1",
            "subreddit": "forhire",
            "title": "[Hiring] Need Python Developer to build an automated data sync pipeline for Shopify & Supabase",
            "content": "Hi! We run an e-commerce brand and need a Python script or microservice to pull product catalog and inventory updates from Shopify API and sync with our Postgres/Supabase DB hourly. Budget: $600 fixed price. Timeline: 1-2 weeks. Please DM or message with your GitHub and experience with Shopify API.",
            "url": "https://reddit.com/r/forhire/comments/sample_r1",
            "author": "ecom_founder_99",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 4,
        },
        {
            "source": "reddit",
            "post_id": "sample_r2",
            "subreddit": "startups",
            "title": "Looking for a full-stack engineer to build a Next.js MVP landing page with Stripe billing",
            "content": "Building an AI writing tool. I have Figma designs ready and need someone to code the frontend in Next.js/Tailwind and wire up Stripe subscription checkout and Supabase auth. Budget: $1,200. Need it done within 10 days. Reach out at founder@writenext.ai or reddit message with past Next.js work.",
            "url": "https://reddit.com/r/startups/comments/sample_r2",
            "author": "writenext_dan",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 8,
        },
        {
            "source": "reddit",
            "post_id": "sample_r3",
            "subreddit": "freelance_forhire",
            "title": "[Hiring] FastAPI developer to fix webhook timeout bugs and optimize database queries",
            "content": "We have an existing FastAPI backend running on Docker. Under peak traffic, our webhook endpoints take >3s and drop connections. Need an experienced backend dev to profile endpoints, add Redis caching, and fix slow SQL queries. Budget: $40/hr, estimated 20-30 hours. Start immediately.",
            "url": "https://reddit.com/r/freelance_forhire/comments/sample_r3",
            "author": "fintech_lead",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 12,
        },
        {
            "source": "reddit",
            "post_id": "sample_r4",
            "subreddit": "SideProject",
            "title": "Need help implementing RAG with Gemini or OpenAI for customer documentation search",
            "content": "Hey all, I have a SaaS product with ~500 documentation pages in Markdown. Looking for a freelance engineer who can build a clean semantic search / RAG assistant endpoint that answers questions with source citations. Budget: $750. Please share relevant projects you have built.",
            "url": "https://reddit.com/r/SideProject/comments/sample_r4",
            "author": "marcus_builds",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 14,
        },
        {
            "source": "reddit",
            "post_id": "sample_r5",
            "subreddit": "forhire",
            "title": "[Hiring] Need a developer to scrape 15 real estate directory sites and structure into JSON",
            "content": "Looking for someone skilled in Python (BeautifulSoup / Playwright) to extract listings across 15 directories without getting blocked. Budget: $500. Timeline: 5 days. Must deliver tested scripts and sample CSV/JSON.",
            "url": "https://reddit.com/r/forhire/comments/sample_r5",
            "author": "proptech_tim",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 18,
        },
        {
            "source": "reddit",
            "post_id": "sample_r6",
            "subreddit": "webdev",
            "title": "Need senior React/TypeScript engineer for 2-week sprint to refactor legacy state management to Zustand",
            "content": "Our team needs an extra pair of hands. Complex React app with messy prop drilling. Need clean Zustand stores and test coverage. Rate: $50/hour, up to 30 hours. Remote. Send code samples or GitHub.",
            "url": "https://reddit.com/r/webdev/comments/sample_r6",
            "author": "sarah_cto",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 20,
        },
        {
            "source": "reddit",
            "post_id": "sample_r7",
            "subreddit": "forhire",
            "title": "[Hiring] Python script to monitor Telegram channels and alert on specific keywords",
            "content": "Need a lightweight background daemon using Telethon / python-telegram-bot to listen to 10 public channels and send webhook alerts to Discord when target coins/keywords appear. Budget: $300. 3 days turnaround.",
            "url": "https://reddit.com/r/forhire/comments/sample_r7",
            "author": "crypto_watcher",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 22,
        },
        {
            "source": "reddit",
            "post_id": "sample_r8",
            "subreddit": "startups",
            "title": "Need developer to build iOS & Android mobile app in React Native - unpaid / equity only",
            "content": "Revolutionary social media idea! Looking for a technical cofounder to build the app from scratch. We offer 15% equity upon launch. No cash budget currently.",
            "url": "https://reddit.com/r/startups/comments/sample_r8",
            "author": "dreamer_ceo",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 24,
        },
        {
            "source": "reddit",
            "post_id": "sample_r9",
            "subreddit": "forhire",
            "title": "[For Hire] Full stack developer available for $25/hr - React, Python, Django",
            "content": "I am an experienced developer looking for projects. Available 30 hours a week. Check my portfolio at myportfolio.com.",
            "url": "https://reddit.com/r/forhire/comments/sample_r9",
            "author": "competing_dev",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 26,
        },
        {
            "source": "reddit",
            "post_id": "sample_r10",
            "subreddit": "forhire",
            "title": "[Hiring] Build an AI voice agent prototype using Twilio and Whisper / Gemini",
            "content": "Need a proof-of-concept where incoming phone calls are transcribed and answered with Gemini in real time. Budget: $1,500. Timeline: 2-3 weeks. Reach out with your voice/AI experience.",
            "url": "https://reddit.com/r/forhire/comments/sample_r10",
            "author": "telecom_innovate",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 28,
        },
        {
            "source": "reddit",
            "post_id": "sample_r11",
            "subreddit": "Python",
            "title": "Contract: Write automated pytest test suite for Django REST backend",
            "content": "Our Django API lacks test coverage before our security audit. Need someone to write unit and integration tests with pytest-django and factory-boy. Budget: $800. Must have experience with CI/CD.",
            "url": "https://reddit.com/r/Python/comments/sample_r11",
            "author": "audit_ready",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 30,
        },
        {
            "source": "reddit",
            "post_id": "sample_r12",
            "subreddit": "forhire",
            "title": "[Hiring] Simple landing page in HTML/CSS - will pay $15 or exposure on my 50k Instagram",
            "content": "Looking for a student who wants experience and a shoutout. Will pay $15.",
            "url": "https://reddit.com/r/forhire/comments/sample_r12",
            "author": "influencer_guy",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 32,
        },
        {
            "source": "reddit",
            "post_id": "sample_r13",
            "subreddit": "startups",
            "title": "Need a developer to integrate Stripe Custom Connect for our marketplace",
            "content": "We need someone who has worked with Stripe Connect onboardings and split payments. We use Node.js and PostgreSQL. Budget: $900 fixed. Estimated 1-2 weeks.",
            "url": "https://reddit.com/r/startups/comments/sample_r13",
            "author": "marketplace_ops",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 34,
        },
        {
            "source": "reddit",
            "post_id": "sample_r14",
            "subreddit": "webdev",
            "title": "Need an expert to migrate our SQLite database to managed Supabase Postgres without downtime",
            "content": "Our hobby project exploded to 40k users. We need a clean migration script, foreign key verification, and zero data loss. Budget: $450. Contact via reddit PM.",
            "url": "https://reddit.com/r/webdev/comments/sample_r14",
            "author": "growth_hacker",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 36,
        },
        {
            "source": "reddit",
            "post_id": "sample_r15",
            "subreddit": "SideProject",
            "title": "Looking for freelance developer to build Chrome Extension with side panel and API sync",
            "content": "Manifest V3 Chrome extension. Needs to capture text on selection, query our backend API, and display results in side panel. Budget: $600. Have design mockups ready.",
            "url": "https://reddit.com/r/SideProject/comments/sample_r15",
            "author": "ext_builder",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 38,
        },
        {
            "source": "reddit",
            "post_id": "sample_r16",
            "subreddit": "forhire",
            "title": "[Hiring] Python data analyst to build automated reporting dashboard in Streamlit",
            "content": "We have Google Sheets data from marketing campaigns that we want visualized in a clean Streamlit dashboard with weekly PDF export. Budget: $400. Need completed by next Monday.",
            "url": "https://reddit.com/r/forhire/comments/sample_r16",
            "author": "growth_agency_jake",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 40,
        },
        {
            "source": "reddit",
            "post_id": "sample_r17",
            "subreddit": "freelance_forhire",
            "title": "[Hiring] Need backend developer to build authentication and user roles in FastAPI",
            "content": "FastAPI with JWT tokens, refresh tokens, role-based access control (Admin, Member, Viewer), and email verification. Budget: $500. Timeline: 4-5 days.",
            "url": "https://reddit.com/r/freelance_forhire/comments/sample_r17",
            "author": "auth_task",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 42,
        },
        {
            "source": "reddit",
            "post_id": "sample_r18",
            "subreddit": "forhire",
            "title": "[For Hire] Graphic Designer for logos and UI banners",
            "content": "Available for commission work. Logos starting at $50.",
            "url": "https://reddit.com/r/forhire/comments/sample_r18",
            "author": "pixel_artist",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 44,
        },
        {
            "source": "reddit",
            "post_id": "sample_r19",
            "subreddit": "SideProject",
            "title": "Need a developer to containerize our multi-service app with Docker Compose and set up GitHub Actions CI",
            "content": "App consists of FastAPI backend, React frontend, Redis, and Celery workers. Want a one-command `docker compose up` setup and GitHub Actions lint/test pipeline. Budget: $350. Turnaround: 2 days.",
            "url": "https://reddit.com/r/SideProject/comments/sample_r19",
            "author": "devops_needed",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 46,
        },
        {
            "source": "reddit",
            "post_id": "sample_r20",
            "subreddit": "startups",
            "title": "Seeking developer to build a RAG knowledge base for legal contracts using Python",
            "content": "We need an internal tool where our legal team can upload PDF contracts and ask questions against clauses with exact page references. Budget: $2,000. Timeline: 3 weeks. Looking for proven experience with Python, embeddings, and document parsing.",
            "url": "https://reddit.com/r/startups/comments/sample_r20",
            "author": "legaltech_alex",
            "created_utc": datetime.now(timezone.utc).timestamp() - 3600 * 47,
        },
    ]
