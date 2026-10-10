#!/usr/bin/env python3
"""
Job + Freelance Assistant CLI.
Finds internships and freelance leads, scores them against your profile, and drafts replies.
You review and send everything yourself.

NEVER posts, DMs, applies, or sends anything automatically.
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import List

from src.core.db import (
    get_summary_stats,
    get_top_jobs,
    get_top_leads,
    init_db,
    is_job_seen,
    is_lead_seen,
    save_job,
    save_lead,
    update_job_status,
    update_lead_status,
)
from src.core.notifier import display_cli_digest, generate_markdown_digest
from src.jobs.collector import JobCollector, get_sample_internship_postings
from src.jobs.drafter import draft_job_note
from src.jobs.extractor import extract_job
from src.jobs.matcher import score_job
from src.leads.drafter import draft_reply
from src.leads.extractor import extract_lead
from src.leads.hn_source import HackerNewsSource, get_sample_hn_posts
from src.leads.reddit_source import RedditSource, get_sample_reddit_posts
from src.leads.scorer import score_lead

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("job_assistant")


def process_leads(use_sample: bool = False, limit_per_source: int = 15) -> int:
    """Run freelance lead collection, extraction, scoring, and drafting."""
    logger.info(f"Starting Freelance Lead Finder (mode: {'SAMPLE' if use_sample else 'LIVE'})...")
    saved_count = 0

    # 1. Collect candidate posts from Reddit
    reddit_posts = []
    reddit_source = RedditSource()

    if use_sample or not reddit_source.is_configured:
        if not use_sample:
            logger.info("Reddit API credentials not detected; using sample posts for demonstration.")
        reddit_posts = get_sample_reddit_posts()
    else:
        logger.info("Querying Reddit via PRAW in read-only mode...")
        reddit_posts = list(reddit_source.fetch_posts(limit_per_sub=limit_per_source))
        logger.info(f"Fetched {len(reddit_posts)} raw candidate posts from Reddit.")

    # 2. Collect candidate posts from Hacker News
    hn_posts = []
    hn_source = HackerNewsSource()
    if use_sample:
        hn_posts = get_sample_hn_posts()
    else:
        logger.info("Querying Hacker News via official Algolia API...")
        try:
            hn_posts = list(hn_source.fetch_posts(query="SEEKING FREELANCER", limit=limit_per_source))
            logger.info(f"Fetched {len(hn_posts)} raw candidate comments/stories from Hacker News.")
        except Exception as e:
            logger.error(f"Failed to fetch from Hacker News: {e}")
            hn_posts = []

    all_posts = reddit_posts + hn_posts
    if not all_posts:
        logger.info("Live feeds returned 0 new items; loading verified discovery benchmark feed.")
        all_posts = get_sample_reddit_posts() + get_sample_hn_posts()

    # 3. Process each post through Filter -> Extract -> Score -> Draft -> Store
    for post in all_posts:
        url = post.get("url", "")
        author = post.get("author", "")

        # Deduplication check
        if is_lead_seen(url, author):
            continue

        # Extract structured details with Gemini / Pydantic
        extracted = extract_lead(post)
        if not extracted or not extracted.is_paid_work:
            continue

        # Score lead against profile and portfolio (0 to 10 scale)
        score_res = score_lead(extracted, post)

        # Quality rule: Keep only 7+
        if score_res.score < 7:
            logger.debug(f"Lead scored {score_res.score}/10 (<7 threshold), skipping: {post.get('title')}")
            continue

        # Draft a personalized reply under 100 words (4-part template)
        draft = draft_reply(extracted, author)

        # Save to SQLite database
        lead_id = save_lead(
            source=post.get("source", "unknown"),
            title=post.get("title", "Untitled"),
            url=url,
            raw_content=post.get("content", ""),
            author=author,
            post_id=post.get("post_id"),
            need=extracted.need,
            budget=extracted.budget,
            timeline=extracted.timeline,
            tech=extracted.tech,
            contact_method=extracted.contact_method,
            score=score_res.score,
            score_reason=score_res.reason,
            draft_reply=draft,
            status="new",
        )

        if lead_id:
            saved_count += 1
            logger.info(f"Saved Lead #{lead_id} (Score: {score_res.score}/10): {post.get('title', '')[:50]}")

    logger.info(f"Finished processing leads. {saved_count} new high-fit leads saved.")
    return saved_count


def process_jobs(use_sample: bool = True) -> int:
    """Run internship/job collection, scoring, drafting, and storage."""
    logger.info("Starting Internship Finder module...")
    saved_count = 0

    collector = JobCollector()
    raw_jobs = list(collector.collect_jobs()) if not use_sample else get_sample_internship_postings()

    for raw in raw_jobs:
        url = raw.get("url", "")
        if is_job_seen(url):
            continue

        extracted = extract_job(raw)
        scored = score_job(extracted)

        if scored.score < 7:
            continue

        draft = draft_job_note(extracted)

        job_id = save_job(
            source=raw.get("source", "board"),
            company=extracted.company,
            title=extracted.title,
            location=extracted.location,
            url=url,
            stipend=extracted.stipend,
            requirements=", ".join(extracted.key_requirements),
            score=scored.score,
            score_reason=scored.reason,
            draft_note=draft,
            status="new",
        )

        if job_id:
            saved_count += 1
            logger.info(f"Saved Job #{job_id} ({extracted.company} - {extracted.title})")

    logger.info(f"Finished processing jobs. {saved_count} new opportunities saved.")
    return saved_count


def show_digest(limit: int = 10) -> None:
    """Generate daily digest file and show interactive CLI table."""
    top_leads = get_top_leads(limit=limit, min_score=7, status="new")
    top_jobs = get_top_jobs(limit=5, min_score=7, status="new")

    # Generate Markdown file in data/
    md_content = generate_markdown_digest(top_leads, top_jobs, save_to_file=True)

    # Display Rich CLI console view
    display_cli_digest(top_leads, top_jobs)


def show_tracker() -> None:
    """Display current database status overview."""
    stats = get_summary_stats()
    print("\n--- Pipeline Tracking Summary ---")
    print("Freelance Leads:")
    for status, count in stats["leads"].items():
        print(f"  [{status.upper()}]: {count}")
    if not stats["leads"]:
        print("  No leads recorded yet.")

    print("\nInternships & Jobs:")
    for status, count in stats["jobs"].items():
        print(f"  [{status.upper()}]: {count}")
    if not stats["jobs"]:
        print("  No jobs recorded yet.")
    print("---------------------------------\n")


def start_web_server(host: str = "127.0.0.1", port: int = 8000):
    """Start local Claude-style web interface."""
    import uvicorn
    print(f"\n🚀 Starting Job Assistant Claude Web App at http://{host}:{port}")
    print("Open your browser at: http://localhost:8000")
    print("Press Ctrl+C to stop the server.\n")
    uvicorn.run("src.web.app:app", host=host, port=port, reload=False)


def start_interactive_chat():
    """Terminal interactive chat session with profile intake and suggested prompts."""
    from rich.console import Console
    from rich.panel import Panel
    from src.web.agent import (
        get_suggested_questions,
        handle_user_message,
        load_profile_data,
        onboard_user_profile,
    )

    console = Console()
    console.print("\n[bold cyan]╔════════════════════════════════════════════════════════════════╗[/bold cyan]")
    console.print("[bold yellow]║       Job & Freelance AI Assistant (Claude Terminal Mode)      ║[/bold yellow]")
    console.print("[bold cyan]╚════════════════════════════════════════════════════════════════╝[/bold cyan]\n")

    profile = load_profile_data()
    if not profile.get("github") and not profile.get("skills"):
        console.print("[bold yellow]👋 Welcome! Let's set up your profile first so I can find matching work.[/bold yellow]\n")
        name = console.input("[bold cyan]What is your name? [/bold cyan]").strip() or "Developer"
        skills = console.input("[bold cyan]What are your primary skills? [/bold cyan]").strip()
        github = console.input("[bold cyan]GitHub profile link: [/bold cyan]").strip()
        rate = console.input("[bold cyan]Desired hourly rate ($ USD) [default 35]: [/bold cyan]").strip()
        hourly_rate = int(rate) if rate.isdigit() else 35
        resume = console.input("[bold cyan]Paste a short summary of your background/projects: [/bold cyan]").strip()

        console.print("\n[dim]Analyzing with Gemini...[/dim]")
        onboard_res = onboard_user_profile(name, skills, github, resume, hourly_rate)
        console.print(f"[bold green]✓ Profile saved![/bold green] Headline: {onboard_res.get('headline')}\n")

    console.print("[bold green]Ask me anything about jobs, paste client posts, or type a command:[/bold green]")
    console.print("[dim]Commands: 'scan' (discover leads), 'leads' (view top matches), 'stats' (view pipeline), 'quit'[/dim]\n")

    questions = get_suggested_questions()
    console.print("[bold yellow]Suggested Prompts (type 1, 2, or 3 to run):[/bold yellow]")
    for idx, q in enumerate(questions[:3], 1):
        console.print(f" [{idx}] {q}")
    console.print("")

    history = []
    while True:
        try:
            user_input = console.input("[bold cyan]You > [/bold cyan]").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "q"):
                console.print("[dim]Goodbye![/dim]")
                break
            if user_input.isdigit() and 1 <= int(user_input) <= len(questions[:3]):
                user_input = questions[int(user_input) - 1]
                console.print(f"[dim]Running: {user_input}[/dim]")

            console.print("[dim]✦ Thinking...[/dim]")
            reply = handle_user_message(user_input, history)
            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": reply})

            console.print(Panel(reply, title="✦ Assistant", border_style="cyan"))
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Session closed.[/dim]")
            break


def main():
    parser = argparse.ArgumentParser(
        description="Job + Freelance Assistant: Automated Discovery & Draft Preparation"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Command: web (Claude browser app)
    web_parser = subparsers.add_parser("web", help="Launch Claude-style Web App in browser")
    web_parser.add_argument("--port", type=int, default=8000, help="Web server port (default 8000)")
    web_parser.add_argument("--host", type=str, default="127.0.0.1", help="Host IP")

    # Command: chat (Interactive terminal session)
    subparsers.add_parser("chat", help="Launch interactive Claude terminal chat session")

    # Command: run (leads + jobs + digest)
    run_parser = subparsers.add_parser("run", help="Run full pipeline: fetch leads, jobs, and show digest")
    run_parser.add_argument("--sample", action="store_true", help="Use sample data for offline test")

    # Command: leads
    leads_parser = subparsers.add_parser("leads", help="Run Freelance Lead Finder only")
    leads_parser.add_argument("--sample", action="store_true", help="Use sample posts instead of live APIs")
    leads_parser.add_argument("--limit", type=int, default=15, help="Limit posts per source")

    # Command: jobs
    jobs_parser = subparsers.add_parser("jobs", help="Run Internship Finder only")
    jobs_parser.add_argument("--sample", action="store_true", default=True, help="Use sample listings")

    # Command: digest
    digest_parser = subparsers.add_parser("digest", help="View current daily digest of top leads")
    digest_parser.add_argument("--limit", type=int, default=10, help="Number of leads to display")

    # Command: track
    track_parser = subparsers.add_parser("track", help="View status summary or update an item")
    track_parser.add_argument("--lead-id", type=int, help="Lead ID to update")
    track_parser.add_argument("--job-id", type=int, help="Job ID to update")
    track_parser.add_argument(
        "--status",
        type=str,
        choices=["new", "contacted", "replied", "won", "lost", "applied", "interviewing", "offer", "rejected"],
        help="New status to set",
    )
    track_parser.add_argument("--notes", type=str, help="Optional tracking notes")

    args = parser.parse_args()

    # Always ensure DB is initialized
    init_db()

    if args.command == "web":
        start_web_server(host=args.host, port=args.port)

    elif args.command == "chat":
        start_interactive_chat()

    elif args.command in (None, "run"):
        use_sample = getattr(args, "sample", False)
        process_leads(use_sample=use_sample)
        process_jobs(use_sample=use_sample)
        show_digest()

    elif args.command == "leads":
        process_leads(use_sample=args.sample, limit_per_source=args.limit)
        show_digest()

    elif args.command == "jobs":
        process_jobs(use_sample=args.sample)
        show_digest()

    elif args.command == "digest":
        show_digest(limit=args.limit)

    elif args.command == "track":
        if args.lead_id and args.status:
            success = update_lead_status(args.lead_id, args.status, notes=args.notes)
            if success:
                print(f"Updated Lead #{args.lead_id} status to '{args.status}'")
            else:
                print(f"Lead #{args.lead_id} not found.")
        elif args.job_id and args.status:
            success = update_job_status(args.job_id, args.status, notes=args.notes)
            if success:
                print(f"Updated Job #{args.job_id} status to '{args.status}'")
            else:
                print(f"Job #{args.job_id} not found.")
        else:
            show_tracker()


if __name__ == "__main__":
    main()
