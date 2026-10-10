"""
Lead Scorer module.
Scores candidate freelance leads from 0 to 10 based on:
1. Skill & tech stack fit with profile.yaml
2. Relevance to case studies in data/portfolio.md
3. Realistic budget (hourly >= $35 or project >= $250)
4. Clarity of the client's request
5. Low competition / early post advantage

Only leads scoring 7 or higher move on to drafting.
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from pydantic import BaseModel, Field
import yaml

from src.core.llm import get_llm_client
from src.leads.extractor import LeadExtraction

logger = logging.getLogger("job_assistant.scorer")


class LeadScoreResult(BaseModel):
    score: int = Field(ge=0, le=10, description="Overall match score from 0 to 10.")
    reason: str = Field(description="1-2 sentences explaining why this score was assigned.")
    skill_fit: int = Field(ge=0, le=10, description="Fit with primary and AI skills.")
    budget_fit: int = Field(ge=0, le=10, description="Adequacy and realism of budget.")
    clarity: int = Field(ge=0, le=10, description="Clarity of requirements.")


def load_profile() -> Dict[str, Any]:
    path = Path(__file__).resolve().parent.parent.parent / "config" / "profile.yaml"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_portfolio_text() -> str:
    path = Path(__file__).resolve().parent.parent.parent / "data" / "portfolio.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def heuristic_score_lead(extraction: LeadExtraction, profile: Dict[str, Any]) -> LeadScoreResult:
    """Heuristic scoring when Gemini API is offline."""
    primary_skills = [s.lower() for s in profile.get("skills", {}).get("primary", [])]
    ai_skills = [s.lower() for s in profile.get("skills", {}).get("ai_and_ml", [])]
    all_skills = primary_skills + ai_skills

    lead_techs = [t.lower() for t in extraction.tech]
    need_lower = extraction.need.lower()

    # Skill match count
    matched = sum(1 for t in lead_techs if any(s in t or t in s for s in all_skills))
    if not matched and any(s in need_lower for s in all_skills):
        matched = 1

    skill_score = min(10, 4 + matched * 2) if matched > 0 else 3

    # Budget score
    budget_lower = extraction.budget.lower()
    if "unpaid" in budget_lower or "equity" in budget_lower:
        budget_score = 0
    elif any(char.isdigit() for char in extraction.budget):
        budget_score = 8
    else:
        budget_score = 5

    # Clarity score
    clarity_score = 8 if len(extraction.need) > 25 and extraction.contact_method != "N/A" else 5

    final_score = int(round((skill_score * 0.5) + (budget_score * 0.3) + (clarity_score * 0.2)))
    final_score = max(0, min(10, final_score))

    reason = f"Matches skills: {', '.join(extraction.tech) or 'General'} | Budget: {extraction.budget}"
    return LeadScoreResult(
        score=final_score,
        reason=reason,
        skill_fit=skill_score,
        budget_fit=budget_score,
        clarity=clarity_score,
    )


def score_lead(extraction: LeadExtraction, raw_post: Optional[Dict[str, Any]] = None) -> LeadScoreResult:
    """
    Evaluates lead against profile.yaml and portfolio.md.
    Returns LeadScoreResult with score 0-10 and rationale.
    """
    profile = load_profile()
    portfolio = load_portfolio_text()
    llm = get_llm_client()

    if not llm.is_available:
        return heuristic_score_lead(extraction, profile)

    prompt = f"""Evaluate this freelance lead for fit with developer profile and case studies.

Lead Details:
- Need: {extraction.need}
- Tech Stack: {', '.join(extraction.tech) if extraction.tech else 'Not specified'}
- Budget: {extraction.budget}
- Timeline: {extraction.timeline}
- Contact: {extraction.contact_method}
- Source URL: {extraction.url}

Developer Profile:
```yaml
{yaml.dump(profile)}
```

Developer Portfolio Case Studies:
\"\"\"
{portfolio}
\"\"\"

Scoring Rules (0 to 10 scale):
- 9-10: Perfect match with Python/FastAPI/Next.js/AI, verified realistic budget ($250+ or $35+/hr), crystal clear need.
- 7-8: Strong skill overlap, good budget or negotiable with solid potential.
- 5-6: Weak skill overlap or vague budget.
- 0-4: Completely out of stack, unrealistic budget, or low value.

Assign an overall score 0-10, component scores, and a concise 1-2 sentence reason.
"""

    system_instruction = (
        "You are an objective freelance project evaluator. "
        "Score leads rigorously. High scores (>= 7) must have real potential and skill alignment."
    )

    scored = llm.generate_structured(
        prompt=prompt,
        response_model=LeadScoreResult,
        system_instruction=system_instruction,
    )

    if scored is None:
        logger.warning(f"LLM scoring failed for {extraction.url}; using heuristic fallback.")
        return heuristic_score_lead(extraction, profile)

    return scored
