"""
Job Drafter module for Module A.
Drafts a concise intro note for job/internship applications under 100 words.
Incorporates relevant project link from data/projects.md.
Never submits applications automatically.
"""

import logging
from pathlib import Path
from typing import Optional

from src.core.llm import get_llm_client
from src.jobs.extractor import JobExtraction

logger = logging.getLogger("job_assistant.job_drafter")


def load_projects_text() -> str:
    path = Path(__file__).resolve().parent.parent.parent / "data" / "projects.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def draft_job_note(extraction: JobExtraction) -> str:
    """Drafts a short introduction message tailored to the role."""
    llm = get_llm_client()
    projects = load_projects_text()

    if not llm.is_available:
        return (
            f"Dear {extraction.company} Hiring Team,\n\n"
            f"I am writing to express my interest in the {extraction.title} position. "
            f"With experience in Python, web architecture, and building agentic pipelines "
            f"(see my work: https://github.com/example/job-ai-project), I would love to bring "
            f"my engineering skills to your team. Looking forward to discussing how I can contribute.\n\n"
            f"Best regards,\nDeveloper"
        )

    prompt = f"""Write a short intro note / cover message (under 90 words) for an application to {extraction.company} for the role of {extraction.title}.
Requirements: {', '.join(extraction.key_requirements)}

Candidate projects:
\"\"\"
{projects}
\"\"\"

Requirements:
- Under 90 words.
- Specific to company and role.
- Include 1 project link.
- Enthusiastic and professional.
"""
    note = llm.generate_text(prompt, temperature=0.3)
    return note.strip() if note else "Application intro note template."
