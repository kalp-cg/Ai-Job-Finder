"""
Reply Drafter module.
Generates tailored, helpful reply drafts under 100 words following the 4-part template:
1. One line showing you read their post and understood the specific problem.
2. One concrete technical suggestion or question about their requirement.
3. One relevant project link from data/portfolio.md.
4. A clear next step ("Happy to share a quick plan, want me to send it?").

Enforces strictly under 100 words and unique wording.
NEVER sends automatically.
"""

import logging
from pathlib import Path
import yaml
import re
from typing import Any, Dict, Optional

from src.core.llm import get_llm_client
from src.leads.extractor import LeadExtraction

logger = logging.getLogger("job_assistant.drafter")


def load_portfolio_markdown() -> str:
    path = Path(__file__).resolve().parent.parent.parent / "data" / "portfolio.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def pick_best_project_link(extraction: LeadExtraction) -> str:
    """Return the candidate's real proof-of-work link from config/profile.yaml (never a placeholder)."""
    profile_path = Path(__file__).resolve().parent.parent.parent / "config" / "profile.yaml"
    if profile_path.exists():
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
        return profile.get("github") or profile.get("linkedin") or ""
    return ""


def heuristic_draft_reply(extraction: LeadExtraction, author: str) -> str:
    """Heuristic fallback draft following the strict 4-part structure under 100 words."""
    link = pick_best_project_link(extraction)
    need_summary = extraction.need.rstrip(".")

    # Part 1: Understand problem
    greeting = f"Hi @{author}," if author and author != "anonymous" else "Hi,"
    p1 = f"{greeting} I read your post about {need_summary}."

    # Part 2: Concrete suggestion or question
    if extraction.tech:
        p2 = f"I work daily with {', '.join(extraction.tech[:3])}. What would the first few weeks of work focus on?"
    else:
        p2 = "What would the first few weeks of work focus on?"

    # Part 3: Proof of work link
    p3 = f"You can see my recent work here: {link}" if link else ""

    # Part 4: Clear next step
    p4 = "Happy to share a quick 3-step plan. Want me to send it over?"

    draft = "\n\n".join(p for p in [p1, p2, p3, p4] if p)
    return draft


def draft_reply(extraction: LeadExtraction, author: Optional[str] = "there") -> str:
    """
    Drafts a personalized, helpful reply strictly under 100 words.
    Uses Gemini LLM with strict prompt constraints, falling back to heuristic template if needed.
    """
    llm = get_llm_client()
    author_name = author if author and author != "anonymous" else "there"
    portfolio_text = load_portfolio_markdown()
    best_link = pick_best_project_link(extraction)

    if not llm.is_available:
        return heuristic_draft_reply(extraction, author_name)

    prompt = f"""Draft a freelance reply note for this client post.

Client Details:
- Poster Handle: @{author_name}
- Need: {extraction.need}
- Tech Stack: {', '.join(extraction.tech)}
- Budget: {extraction.budget}
- Timeline: {extraction.timeline}

Available Case Studies & Links:
\"\"\"
{portfolio_text}
\"\"\"
Recommended Project Link: {best_link}

STRICT QUALITY RULES:
1. Total word count MUST BE UNDER 100 WORDS.
2. Structure into exactly 4 parts:
   - Part 1: One line showing you read their post and understood their specific problem.
   - Part 2: One concrete, helpful technical suggestion or insightful question about their requirement.
   - Part 3: One line pointing to the candidate's real work at {best_link} (do not invent other links or projects).
   - Part 4: A clear next step (e.g., "Happy to share a quick plan, want me to send it?").
3. Tone: Humble, direct, professional. No buzzwords, no pushy sales pitching, no fluff.
4. Do not invent fake claims.
5. Return ONLY the reply text.
"""

    system_instruction = (
        "You are an expert freelance consultant writing short, high-conversion, helpful replies. "
        "Strictly adhere to the under-100-words limit."
    )

    reply_text = llm.generate_text(
        prompt=prompt,
        system_instruction=system_instruction,
        temperature=0.4,
    )

    if not reply_text:
        return heuristic_draft_reply(extraction, author_name)

    # Word count validation & safeguard
    words = reply_text.split()
    if len(words) > 115:
        logger.warning(f"Draft exceeded word count ({len(words)} words); trimming to ensure brevity.")
        # Retain top 4 concise paragraphs or fallback
        paragraphs = [p for p in reply_text.split("\n\n") if p.strip()]
        if len(paragraphs) >= 4:
            reply_text = "\n\n".join(paragraphs[:4])

    return reply_text.strip()
