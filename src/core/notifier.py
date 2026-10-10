"""
Notifier and Digest Generator.
Creates daily digests of top leads and jobs for manual review and human sending.
NEVER sends automatic messages or applies. All output is prepared for the user to review.
"""

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

logger = logging.getLogger("job_assistant.notifier")
console = Console()


def format_lead_card(lead: Dict[str, Any], index: int) -> str:
    """Format a single lead as a markdown card for review."""
    tech_str = ", ".join(lead.get("tech", [])) if lead.get("tech") else "Not specified"
    card = f"""### [{index}] {lead.get('title', 'No Title')}
- **Source:** {lead.get('source', '').upper()}
- **Author:** @{lead.get('author', 'anonymous')}
- **Post URL:** {lead.get('url')}
- **Score:** {lead.get('score', 0)}/10 — {lead.get('score_reason', 'N/A')}
- **Need:** {lead.get('need', 'N/A')}
- **Budget:** {lead.get('budget', 'N/A')} | **Timeline:** {lead.get('timeline', 'N/A')}
- **Tech Stack:** {tech_str}
- **Contact:** {lead.get('contact_method', 'Via post/DM')}

**Draft Reply (Ready to review & send by hand):**
```text
{lead.get('draft_reply', 'No draft generated.')}
```
---
"""
    return card


def format_job_card(job: Dict[str, Any], index: int) -> str:
    """Format an internship/job as a markdown card for review."""
    card = f"""### [{index}] {job.get('title', 'Role')} @ {job.get('company', 'Company')}
- **Source:** {job.get('source', '').upper()}
- **Location:** {job.get('location', 'Remote')}
- **URL:** {job.get('url')}
- **Score:** {job.get('score', 0)}/10 — {job.get('score_reason', 'N/A')}
- **Stipend/Comp:** {job.get('stipend', 'N/A')}
- **Requirements:** {job.get('requirements', 'N/A')}

**Application Intro Note:**
```text
{job.get('draft_note', 'No note generated.')}
```
---
"""
    return card


def generate_markdown_digest(
    leads: List[Dict[str, Any]],
    jobs: Optional[List[Dict[str, Any]]] = None,
    save_to_file: bool = True,
) -> str:
    """Generate a complete Markdown digest file for today's review."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    md_lines = [
        f"# Job & Freelance Daily Digest — {today_str}",
        "",
        "> **Important:** This assistant never posts or contacts anyone. Review each draft, edit as needed, and send manually.",
        "",
        f"## Top Freelance Leads ({len(leads)} found)",
        "",
    ]

    if not leads:
        md_lines.append("*No new high-scoring leads found today.*\n")
    else:
        for idx, lead in enumerate(leads, 1):
            md_lines.append(format_lead_card(lead, idx))

    if jobs:
        md_lines.append(f"\n## Top Internship & Job Matches ({len(jobs)} found)\n")
        for idx, job in enumerate(jobs, 1):
            md_lines.append(format_job_card(job, idx))

    digest_content = "\n".join(md_lines)

    if save_to_file:
        data_dir = Path(__file__).resolve().parent.parent.parent / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        digest_file = data_dir / f"digest_{today_str}.md"
        digest_file.write_text(digest_content, encoding="utf-8")
        logger.info(f"Daily digest saved to {digest_file}")

    return digest_content


def display_cli_digest(leads: List[Dict[str, Any]], jobs: Optional[List[Dict[str, Any]]] = None) -> None:
    """Display a rich interactive terminal view of the top leads and drafts."""
    console.print("\n[bold cyan]═══════════════════════════════════════════════════════════[/bold cyan]")
    console.print("[bold yellow]       JOB & FREELANCE ASSISTANT: DAILY DIGEST            [/bold yellow]")
    console.print("[bold cyan]═══════════════════════════════════════════════════════════[/bold cyan]\n")
    console.print("[italic dim]Remember: You review and send by hand. No automated messages.[/italic dim]\n")

    if not leads:
        console.print("[yellow]No freelance leads with score >= 7 currently pending.[/yellow]\n")
    else:
        table = Table(title=f"Top {len(leads)} Freelance Leads for Review", show_lines=True)
        table.add_column("ID", style="dim", width=4)
        table.add_column("Score", style="bold green", width=6)
        table.add_column("Source", style="cyan", width=8)
        table.add_column("Poster", style="magenta", width=14)
        table.add_column("Title / Need", style="white", min_width=25)
        table.add_column("Budget", style="yellow", width=12)

        for lead in leads:
            table.add_row(
                str(lead["id"]),
                f"{lead.get('score', 0)}/10",
                lead.get("source", "").upper(),
                f"@{lead.get('author', 'anon')}",
                (lead.get("title") or lead.get("need") or "No title")[:40],
                str(lead.get("budget", "N/A"))[:12],
            )
        console.print(table)

        # Show cards for the top 3 drafts right in CLI
        console.print("\n[bold green]Top Draft Replies to Review:[/bold green]")
        for idx, lead in enumerate(leads[:3], 1):
            draft = lead.get("draft_reply", "No draft available.")
            content = (
                f"[bold cyan]Post:[/bold cyan] {lead.get('title')}\n"
                f"[bold cyan]URL:[/bold cyan] {lead.get('url')}\n"
                f"[bold cyan]Budget:[/bold cyan] {lead.get('budget', 'N/A')} | "
                f"[bold cyan]Timeline:[/bold cyan] {lead.get('timeline', 'N/A')}\n"
                f"[bold cyan]Fit Reason:[/bold cyan] {lead.get('score_reason', 'N/A')}\n\n"
                f"[bold yellow]Draft Reply (Copy & Edit):[/bold yellow]\n{draft}"
            )
            console.print(Panel(content, title=f"Lead #{lead.get('id')} — Score: {lead.get('score')}/10"))

    if jobs:
        console.print("\n[bold green]Internship & Job Matches:[/bold green]")
        job_table = Table(title=f"Top {len(jobs)} Opportunities", show_lines=True)
        job_table.add_column("ID", style="dim", width=4)
        job_table.add_column("Score", style="bold green", width=6)
        job_table.add_column("Company", style="cyan", width=16)
        job_table.add_column("Role", style="white", min_width=20)
        job_table.add_column("URL", style="blue")

        for job in jobs:
            job_table.add_row(
                str(job["id"]),
                f"{job.get('score', 0)}/10",
                job.get("company", "Company"),
                job.get("title", "Role"),
                job.get("url", ""),
            )
        console.print(job_table)
