"""
Job Extractor module for Module A.
Extracts structured role details and requirements using Gemini with Pydantic schema validation.
"""

import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from src.core.llm import get_llm_client

logger = logging.getLogger("job_assistant.job_extractor")


class JobExtraction(BaseModel):
    company: str = Field(description="Company name")
    title: str = Field(description="Job / Internship title")
    location: str = Field(description="Location or Remote status")
    stipend: str = Field(description="Mentioned stipend or salary")
    key_requirements: List[str] = Field(default_factory=list, description="Top 3-4 required skills")
    is_internship_or_entry: bool = Field(description="True if internship, co-op, or junior/entry role")


def heuristic_extract_job(raw_job: Dict[str, Any]) -> JobExtraction:
    """Fallback extraction when LLM is offline."""
    return JobExtraction(
        company=raw_job.get("company", "Company"),
        title=raw_job.get("title", "Software Engineering Intern"),
        location=raw_job.get("location", "Remote"),
        stipend=raw_job.get("stipend", "Competitive Stipend"),
        key_requirements=["Python", "Web APIs", "Problem Solving"],
        is_internship_or_entry="intern" in raw_job.get("title", "").lower(),
    )


def extract_job(raw_job: Dict[str, Any]) -> JobExtraction:
    """Extracts structured job posting details using Gemini."""
    llm = get_llm_client()
    if not llm.is_available:
        return heuristic_extract_job(raw_job)

    prompt = f"""Extract structured information for this job posting:
Company: {raw_job.get('company')}
Title: {raw_job.get('title')}
Location: {raw_job.get('location')}
URL: {raw_job.get('url')}
Description:
\"\"\"
{raw_job.get('description')}
\"\"\"
"""
    system_instruction = "Extract structured role requirements strictly into the JobExtraction schema."

    extracted = llm.generate_structured(prompt=prompt, response_model=JobExtraction, system_instruction=system_instruction)
    if not extracted:
        return heuristic_extract_job(raw_job)
    return extracted
