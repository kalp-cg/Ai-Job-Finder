"""
Conversational Agent Engine for Job & Freelance Assistant.
Handles user chat, pipeline commands, profile onboarding, resume parsing, and reply drafting.
"""

from datetime import datetime, timezone
import io
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
import yaml

from src.core.db import get_summary_stats, get_top_jobs, get_top_leads, update_lead_status
from src.core.llm import get_llm_client
from src.leads.drafter import draft_reply
from src.leads.extractor import LeadExtraction, extract_lead
from src.leads.scorer import score_lead

logger = logging.getLogger("job_assistant.agent")

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def load_profile_data() -> Dict[str, Any]:
    profile_path = CONFIG_DIR / "profile.yaml"
    if profile_path.exists():
        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"Error loading profile.yaml: {e}")
    return {}


def load_resume_text() -> str:
    path = DATA_DIR / "resume.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def load_portfolio_text() -> str:
    path = DATA_DIR / "portfolio.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def get_suggested_questions() -> List[str]:
    return [
        "Scan Reddit & Hacker News for new freelance leads",
        "Find top-rated software & AI internships",
        "Analyze a client post and draft a winning reply",
        "Review my GitHub & portfolio to suggest high-value niches",
    ]


def parse_resume_file(file_bytes: bytes, filename: str) -> Dict[str, Any]:
    """
    Extracts text from an uploaded resume (PDF, TXT, MD) and uses LLM
    to automatically extract skills, headline, links, and target rates.
    """
    text_content = ""
    lower_name = filename.lower()

    if lower_name.endswith(".pdf"):
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            pages = [page.extract_text() or "" for page in reader.pages]
            text_content = "\n".join(pages)
        except Exception as e:
            logger.error(f"Error parsing PDF with pypdf: {e}")
            return {"error": f"Failed to parse PDF: {str(e)}"}
    else:
        text_content = file_bytes.decode("utf-8", errors="ignore")

    if not text_content.strip():
        return {"error": "No readable text found in uploaded file."}

    # Save to data/resume.md
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "resume.md").write_text(text_content, encoding="utf-8")

    # Use LLM to extract structured profile
    llm = get_llm_client()
    extract_prompt = f"""You are an elite career data parser. Analyze this resume text and extract the candidate's core details:

RESUME TEXT:
{text_content[:6000]}

Return ONLY a valid JSON object strictly matching this schema:
{{
  "name": "Candidate Full Name",
  "headline": "Target role or professional title",
  "github": "GitHub profile URL if present, or empty string",
  "linkedin": "LinkedIn profile URL if present, or empty string",
  "primary_skills": ["Python", "FastAPI", "Next.js"],
  "suggested_rate": 45,
  "summary": "2-sentence summary highlighting core strengths"
}}
"""
    if llm.is_available:
        try:
            raw = llm.generate_text(extract_prompt, system_instruction="Return ONLY valid JSON. No markdown fences.")
            if raw:
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
                data = json.loads(raw.strip())
                data["raw_text"] = text_content[:1500]
                return data
        except Exception as e:
            logger.error(f"Error in LLM resume extraction: {e}")

    # Fallback if LLM unavailable
    return {
        "name": "",
        "headline": "Software Engineer",
        "github": "",
        "linkedin": "",
        "primary_skills": ["Python", "JavaScript"],
        "suggested_rate": 35,
        "summary": text_content[:400],
        "raw_text": text_content[:1500],
    }


def onboard_user_profile(
    name: str,
    skills_input: str,
    github_url: str,
    resume_or_bio: str,
    linkedin_url: str = "",
    hourly_rate: int = 35,
    min_budget: int = 250,
    target_roles: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Takes user's inputs, uses Gemini to analyze everything at once, and updates:
    - config/profile.yaml
    - data/resume.md
    - data/portfolio.md
    """
    llm = get_llm_client()

    analysis_prompt = f"""You are an elite developer career and freelance advisor.
Analyze this developer's information and structure it into a clean profile.

Inputs:
- Name: {name}
- Skills: {skills_input}
- GitHub: {github_url}
- LinkedIn: {linkedin_url}
- Resume / Bio / Past work: {resume_or_bio}
- Hourly Rate: ${hourly_rate}/hr
- Min Project Budget: ${min_budget}

Tasks:
1. Categorize skills into primary, ai_and_ml, and infrastructure.
2. Formulate 2-3 compelling case studies with problem, solution, outcome, and keywords based on their past work or projects.
3. Provide a brief professional headline and summary.

Return ONLY a valid JSON object strictly matching this schema:
{{
  "headline": "Full-Stack & AI Systems Engineer",
  "primary_skills": ["Python", "FastAPI"],
  "ai_skills": ["Gemini API", "RAG"],
  "infra_skills": ["Docker", "Git"],
  "case_studies": [
    {{
      "title": "Case Study 1: ...",
      "problem": "...",
      "solution": "...",
      "outcome": "...",
      "keywords": "Python, API",
      "link": "{github_url}"
    }}
  ],
  "recommendations": "2 short tips for winning higher-paying clients"
}}
"""
    system_instruction = "Return ONLY valid JSON matching the requested schema. No markdown fences."

    profile_obj = None
    if llm.is_available:
        try:
            raw_text = llm.generate_text(analysis_prompt, system_instruction=system_instruction)
            if raw_text:
                if raw_text.startswith("```"):
                    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                    raw_text = re.sub(r"\s*```$", "", raw_text)
                profile_obj = json.loads(raw_text.strip())
        except Exception as e:
            logger.error(f"Error parsing Gemini onboarding response: {e}")

    if not profile_obj:
        skills_list = [s.strip() for s in skills_input.split(",") if s.strip()]
        profile_obj = {
            "headline": "Full-Stack Developer",
            "primary_skills": skills_list[:5] or ["Python", "FastAPI"],
            "ai_skills": ["LLM Integration", "RAG"],
            "infra_skills": ["Docker", "Git"],
            "case_studies": [
                {
                    "title": "Production Web & API Platform",
                    "problem": "Client needed a high-performance backend with clean documentation.",
                    "solution": "Built modular FastAPI architecture with automated background pipelines.",
                    "outcome": "Fast response times and seamless client delivery.",
                    "keywords": "FastAPI, Python",
                    "link": github_url,
                }
            ],
            "recommendations": "Highlight specific business metrics and past API integration projects.",
        }

    # 1. Update config/profile.yaml
    profile_yaml_content = {
        "name": name,
        "title": profile_obj.get("headline", "Full-Stack Developer"),
        "github": github_url,
        "linkedin": linkedin_url,
        "hourly_rate_usd": hourly_rate,
        "minimum_project_budget_usd": min_budget,
        "stipend_minimum_monthly_usd": 500,
        "skills": {
            "primary": profile_obj.get("primary_skills", []),
            "ai_and_ml": profile_obj.get("ai_skills", []),
            "infrastructure": profile_obj.get("infra_skills", []),
        },
        "preferences": {
            "roles": target_roles or ["Full-Stack Developer", "Backend Engineer", "AI Engineer"],
            "availability_hours_per_week": 25,
        },
    }

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_DIR / "profile.yaml", "w", encoding="utf-8") as f:
        yaml.dump(profile_yaml_content, f, sort_keys=False, default_flow_style=False)

    # 2. Update data/resume.md
    resume_md_content = f"""# {name} — Resume & Profile

**GitHub:** {github_url}  
**LinkedIn:** {linkedin_url}  
**Headline:** {profile_obj.get('headline')}  
**Target Rate:** ${hourly_rate}/hr  

## Core Skills
- **Primary:** {', '.join(profile_obj.get('primary_skills', []))}
- **AI & Automation:** {', '.join(profile_obj.get('ai_skills', []))}
- **Infrastructure:** {', '.join(profile_obj.get('infra_skills', []))}

## Background & Highlights
{resume_or_bio}
"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "resume.md").write_text(resume_md_content, encoding="utf-8")

    # 3. Update data/portfolio.md
    portfolio_md_lines = ["# Freelance Portfolio & Case Studies\n"]
    for idx, cs in enumerate(profile_obj.get("case_studies", []), 1):
        portfolio_md_lines.append(f"### Case Study {chr(64 + idx)}: {cs.get('title')}")
        portfolio_md_lines.append(f"- **Problem:** {cs.get('problem')}")
        portfolio_md_lines.append(f"- **Solution:** {cs.get('solution')}")
        portfolio_md_lines.append(f"- **Outcome:** {cs.get('outcome')}")
        portfolio_md_lines.append(f"- **Relevant Link:** {cs.get('link') or github_url}")
        portfolio_md_lines.append(f"- **Keywords:** {cs.get('keywords')}\n")

    (DATA_DIR / "portfolio.md").write_text("\n".join(portfolio_md_lines), encoding="utf-8")

    return {
        "status": "success",
        "name": name,
        "headline": profile_obj.get("headline"),
        "primary_skills": profile_obj.get("primary_skills"),
        "case_studies_count": len(profile_obj.get("case_studies", [])),
        "recommendations": profile_obj.get("recommendations"),
    }


def handle_user_message(user_msg: str, conversation_history: Optional[List[Dict[str, str]]] = None) -> str:
    """
    Main conversational agent loop.
    Understands intent, provides answers, and triggers discovery/draft actions.
    """
    user_msg_clean = user_msg.strip()
    msg_lower = user_msg_clean.lower()
    profile = load_profile_data()
    user_name = profile.get("name", "there")

    # Intent 0: Natural Greeting Handling (CONCISE & HUMAN, NO ESSAYS)
    greeting_words = ["hello", "hi", "hey", "sup", "yo", "good morning", "good evening", "hello!", "hey there"]
    if msg_lower in greeting_words or re.match(r"^(hi|hello|hey|yo)\b[!?.]*$", msg_lower):
        has_resume = bool(load_resume_text())
        has_linkedin = bool(profile.get("linkedin"))
        has_github = bool(profile.get("github"))

        if not has_resume or not has_github:
            return (
                f"Hey {user_name}! I'm active and ready to hunt opportunities for you.\n\n"
                f"Whenever you're ready, you can drop your **Resume** or link your **GitHub / LinkedIn** in the Profile modal at the top right to sharpen match accuracy.\n\n"
                f"What are we targeting right now—freelance client contracts or software internships?"
            )
        return (
            f"Hey {user_name}! All systems are active and calibrated to your skills.\n\n"
            f"What would you like to explore today—run a fresh lead scan, inspect top opportunities, or draft a proposal for a client post?"
        )

    # Intent 1: Check stats / pipeline
    if any(k in msg_lower for k in ["stats", "tracker", "funnel", "how many leads", "overview"]):
        stats = get_summary_stats()
        leads_str = ", ".join([f"{k.upper()}: {v}" for k, v in stats["leads"].items()]) or "No leads yet"
        jobs_str = ", ".join([f"{k.upper()}: {v}" for k, v in stats["jobs"].items()]) or "No jobs yet"
        return (
            f"**Pipeline Overview:**\n\n"
            f"- **Freelance Leads:** {leads_str}\n"
            f"- **Internships:** {jobs_str}\n\n"
            f"Want me to run a discovery scan now, or review your top matches?"
        )

    # Intent 2: View top leads
    if any(k in msg_lower for k in ["show leads", "top leads", "view leads", "latest leads", "find leads", "show opportunities"]):
        leads = get_top_leads(limit=5, min_score=7, status="new")
        if not leads:
            return (
                "You don't have any unprocessed leads scoring 7+ in the database right now.\n\n"
                "Ask me to **'scan leads'** to discover fresh postings across Hacker News and Reddit!"
            )

        resp = [f"Here are your top {len(leads)} opportunities scoring 7+:\n"]
        for idx, l in enumerate(leads, 1):
            resp.append(
                f"**{idx}. [{l.get('source', '').upper()}] {l.get('title')}** (Score: {l.get('score')}/10)\n"
                f"- **Budget:** {l.get('budget', 'Undisclosed')} | **Author:** @{l.get('author', 'anon')}\n"
                f"- **Link:** [{l.get('url')}]({l.get('url')})\n\n"
                f"> **Draft Pitch:**\n"
                f"```text\n{l.get('draft_reply', '')}\n```\n"
            )
        return "\n".join(resp)

    # Intent 3: Scan leads or jobs
    if "scan" in msg_lower or "fetch" in msg_lower or "scrape" in msg_lower or "run pipeline" in msg_lower:
        from main import process_jobs, process_leads
        new_leads = process_leads(use_sample=False, limit_per_source=15)
        new_jobs = process_jobs(use_sample=True)
        return (
            f"⚡ **Discovery Complete!**\n\n"
            f"- Discovered **{new_leads} fresh freelance leads**\n"
            f"- Evaluated **{new_jobs} internship postings**\n\n"
            f"You can view them right now in the **Opportunities** drawer at the top right, or tell me: *'show top 3 leads'*."
        )

    # Intent 4: User pastes a client post to draft a reply
    if ("client says" in msg_lower or "post:" in msg_lower or "reply to this" in msg_lower or len(user_msg_clean) > 200):
        extracted = extract_lead({"title": "Client Job Request", "content": user_msg_clean, "url": "custom_chat_input", "author": "client"})
        if extracted and extracted.is_paid_work:
            scored = score_lead(extracted)
            reply = draft_reply(extracted, "there")
            return (
                f"**Client Need:** {extracted.need}\n"
                f"- **Budget:** {extracted.budget} | **Fit Score:** {scored.score}/10\n\n"
                f"> **Tailored Pitch (<100 words):**\n"
                f"```text\n{reply}\n```\n"
                f"*Review and customize before sending.*"
            )

    # General Conversational response with Gemini
    llm = get_llm_client()
    system_instruction = f"""You are JobAI, a sharp, elite career and freelance advisor with the elegance and conciseness of Claude.

Candidate Context:
- Name: {profile.get('name', 'Developer')}
- Rate: ${profile.get('hourly_rate_usd', 35)}/hr
- Skills: {', '.join(profile.get('skills', {}).get('primary', []))}

STRICT RESPONSE RULES:
1. NEVER regurgitate the user's resume back to them unless explicitly asked.
2. NEVER output 4-point numbered lists of what services you can provide for simple questions.
3. Be remarkably concise, human, and direct (1-3 sentences max).
4. If they ask about jobs or freelance work, answer directly and suggest ONE crisp next step.
"""

    if llm.is_available:
        history_context = ""
        if conversation_history:
            for turn in conversation_history[-4:]:
                history_context += f"{turn.get('role', 'user')}: {turn.get('content', '')}\n"

        prompt = f"{history_context}User: {user_msg}\nAssistant:"
        response = llm.generate_text(prompt, system_instruction=system_instruction)
        if response:
            return response.strip()

    return (
        f"I'm here to help you identify contracts, evaluate internships, and draft winning proposals.\n\n"
        f"Ask me to **'scan leads'**, **'show top opportunities'**, or paste a job post to generate a custom proposal."
    )
