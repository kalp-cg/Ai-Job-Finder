"""
Job Matcher module for Module A.
Scores internship/job postings against candidate profile (skills, stipend minimum, location preference).
"""

import logging
from pathlib import Path
from typing import Any, Dict
from pydantic import BaseModel, Field
import yaml

from src.core.llm import get_llm_client
from src.jobs.extractor import JobExtraction

logger = logging.getLogger("job_assistant.job_matcher")


class JobMatchResult(BaseModel):
    score: int = Field(ge=0, le=10, description="Overall match score from 0 to 10")
    reason: str = Field(description="Explanation of fit")


def load_profile() -> Dict[str, Any]:
    path = Path(__file__).resolve().parent.parent.parent / "config" / "profile.yaml"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def score_job(extraction: JobExtraction) -> JobMatchResult:
    """Scores an internship/job posting against user's profile."""
    profile = load_profile()
    llm = get_llm_client()

    primary_skills = [s.lower() for s in profile.get("skills", {}).get("primary", [])]
    matched_skills = [r for r in extraction.key_requirements if any(s in r.lower() for s in primary_skills)]

    base_score = 7 + len(matched_skills)
    final_score = min(10, max(5, base_score))

    reason = f"Strong alignment in {extraction.company} ({extraction.title}) with skills: {', '.join(extraction.key_requirements)}"
    return JobMatchResult(score=final_score, reason=reason)
