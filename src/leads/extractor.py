"""
Lead Extractor module.
Uses Gemini LLM to classify whether a post is a request for paid work,
and extracts structured metadata into a Pydantic model.
Validates schemas strictly and handles errors safely.
"""

import logging
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from src.core.llm import get_llm_client

logger = logging.getLogger("job_assistant.extractor")


class LeadExtraction(BaseModel):
    is_paid_work: bool = Field(
        description="True if this is a client/founder seeking to hire and pay for developer work. False if it is a freelancer advertising their own services, an unpaid request, or spam."
    )
    need: str = Field(
        description="A concise summary of what the client wants built or fixed (1-2 sentences)."
    )
    budget: str = Field(
        description="Mentioned budget or rate (e.g., '$600 fixed', '$40/hr', 'Negotiable', or 'None/Unspecified')."
    )
    timeline: str = Field(
        description="Timeline or deadline requested (e.g., '1-2 weeks', 'Immediate', 'Not specified')."
    )
    tech: List[str] = Field(
        default_factory=list,
        description="Key technologies or frameworks mentioned (e.g. ['Python', 'FastAPI', 'PostgreSQL'])."
    )
    contact_method: str = Field(
        description="How the poster wants to be contacted (e.g., 'Reddit DM', 'Email to founder@...', 'Comment')."
    )
    url: str = Field(
        description="The source URL of the post."
    )
    rejection_reason: Optional[str] = Field(
        default=None,
        description="Explanation if is_paid_work is False."
    )


def heuristic_extract_lead(post: Dict[str, Any]) -> LeadExtraction:
    """
    Rule-based extraction fallback when Gemini API key is not yet configured,
    or during offline testing. Ensures pipeline never crashes.
    """
    text = f"{post.get('title', '')}\n{post.get('content', '')}"
    text_lower = text.lower()

    # Disqualification checks
    is_for_hire = "[for hire]" in text_lower or "i am a developer" in text_lower or "available for hire" in text_lower
    is_unpaid = "unpaid" in text_lower or "equity only" in text_lower or "exposure" in text_lower

    if is_for_hire:
        return LeadExtraction(
            is_paid_work=False,
            need="Freelancer offering services",
            budget="N/A",
            timeline="N/A",
            tech=[],
            contact_method="N/A",
            url=post.get("url", ""),
            rejection_reason="Post is from another freelancer offering services, not a client hiring.",
        )

    if is_unpaid:
        return LeadExtraction(
            is_paid_work=False,
            need="Unpaid or equity-only project",
            budget="Unpaid / Equity",
            timeline="N/A",
            tech=[],
            contact_method="N/A",
            url=post.get("url", ""),
            rejection_reason="Unpaid or equity-only request; violated quality rules.",
        )

    # Detect budget
    budget_match = re.search(r"(\$\s?\d+[\d,]*(?:\s*(?:-\s*\$?\d+[\d,]*))?(?:\s*(?:\/hr|per hour|fixed|usd))?)", text, re.I)
    budget = budget_match.group(1).strip() if budget_match else "Unspecified / Negotiable"

    # Detect timeline
    timeline_match = re.search(r"(\d+\s*(?:day|week|month)s?|immediate(?:ly)?|asap)", text, re.I)
    timeline = timeline_match.group(1).strip() if timeline_match else "Not specified"

    # Detect tech keywords
    known_techs = [
        "Python", "FastAPI", "Django", "Flask", "React", "Next.js", "TypeScript",
        "JavaScript", "Node.js", "Docker", "PostgreSQL", "Supabase", "SQLite",
        "Tailwind", "Stripe", "RAG", "LLM", "Gemini", "OpenAI", "Playwright",
        "BeautifulSoup", "Zustand", "Redis", "Celery", "Streamlit"
    ]
    detected_tech = [t for t in known_techs if re.search(r"\b" + re.escape(t) + r"\b", text, re.I)]

    # Detect contact method
    email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", text)
    if email_match:
        contact_method = f"Email: {email_match.group(0)}"
    elif "dm" in text_lower or "pm" in text_lower:
        contact_method = "Reddit/Direct Message"
    else:
        contact_method = "Reply on post"

    title = post.get("title", "")
    need = title if len(title) > 20 else text.split("\n")[0][:120]

    return LeadExtraction(
        is_paid_work=True,
        need=need.strip(),
        budget=budget,
        timeline=timeline,
        tech=detected_tech,
        contact_method=contact_method,
        url=post.get("url", ""),
    )


def extract_lead(post: Dict[str, Any]) -> Optional[LeadExtraction]:
    """
    Extracts structured lead details using Gemini LLM,
    falling back to heuristic extraction if Gemini is offline.
    """
    llm = get_llm_client()
    url = post.get("url", "")

    if not llm.is_available:
        return heuristic_extract_lead(post)

    prompt = f"""Analyze this public forum post and classify if it is a real request for paid developer freelance work.

Title: {post.get('title')}
Author: {post.get('author')}
URL: {url}

Content:
\"\"\"
{post.get('content')}
\"\"\"

Guidelines:
- is_paid_work MUST be false if the poster is offering their own freelance services (e.g. '[For Hire]').
- is_paid_work MUST be false if there is no payment, or it is equity-only, rev-share, or 'exposure'.
- is_paid_work MUST be true if a client/founder is asking to hire and pay a developer.
- Extract the exact need, budget, timeline, tech stack, and contact method.
- Return the exact post URL provided: {url}
"""

    system_instruction = (
        "You are an expert recruitment and freelance lead extraction engine. "
        "Filter out spam, other freelancers looking for work, and unpaid requests. "
        "Extract structured JSON matching the LeadExtraction schema."
    )

    extracted = llm.generate_structured(
        prompt=prompt,
        response_model=LeadExtraction,
        system_instruction=system_instruction,
    )

    if extracted is None:
        logger.warning(f"Gemini extraction failed or invalid JSON for {url}; using fallback.")
        return heuristic_extract_lead(post)

    return extracted
