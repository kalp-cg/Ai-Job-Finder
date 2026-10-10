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


SCAN_RE = re.compile(r"\b(scan|fetch|scrape|run pipeline|find new)\b")


def is_scan_request(msg_lower: str) -> bool:
    return bool(SCAN_RE.search(msg_lower)) and len(msg_lower) < 120


def handle_lead_message(user_msg: str, lead: Dict[str, Any], history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """
    Chat about one specific lead. Returns {"reply": str, "draft": Optional[str]}.
    `draft` is set only when the user asked for a new or changed reply.
    """
    llm = get_llm_client()
    if not llm.is_available:
        return {"reply": "Gemini isn't configured, so I can't work on this draft. Add GEMINI_API_KEY to .env.", "draft": None}

    profile = load_profile_data()
    skills = profile.get("skills") or {}
    flat_skills = [s for group in skills.values() for s in (group or [])] if isinstance(skills, dict) else list(skills)
    history_text = "\n".join(f"{t.get('role')}: {t.get('content')}" for t in (history or [])[-6:])

    prompt = f"""You are helping {profile.get('name', 'the developer')} respond to one job/freelance post.

CANDIDATE: {profile.get('title', '')}; skills: {', '.join(flat_skills)}; rate ${profile.get('hourly_rate_usd', 35)}/hr; work: {profile.get('github', '')}

POST ({lead.get('source_label') or lead.get('source', '').upper()}, {lead.get('url', '')}):
{lead.get('title', '')} at {lead.get('company', '')}
\"\"\"
{(lead.get('raw_content') or lead.get('description') or lead.get('need') or '')[:4000]}
\"\"\"

WHY IT WAS SCORED {lead.get('score')}/10: {lead.get('score_reason') or lead.get('why', '')}

CURRENT DRAFT:
\"\"\"
{lead.get('draft_reply') or lead.get('draft') or ''}
\"\"\"

RECENT CONVERSATION:
{history_text}

USER: {user_msg}

Respond as JSON: {{"reply": "...", "draft": null}}
- "reply": 1-3 plain sentences answering the user. No emojis, no lists unless asked.
- "draft": the full new draft ONLY if the user asked to write, rewrite, shorten, or change the reply; otherwise null.
  Drafts stay under 150 words, never invent experience or links beyond the candidate info above.
"""
    raw = llm.generate_text(prompt, system_instruction="Return ONLY valid JSON. No markdown fences.")
    if not raw:
        return {"reply": "Gemini didn't respond (likely the daily free quota). Try again later.", "draft": None}
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    try:
        data = json.loads(raw)
        return {"reply": str(data.get("reply") or "").strip(), "draft": (data.get("draft") or None)}
    except Exception:
        return {"reply": raw, "draft": None}


def draft_application_note(item: Dict[str, Any]) -> str:
    """Short application email for an internship/job posting. Uses Gemini when available, else a plain template."""
    profile = load_profile_data()
    name = profile.get("name") or "there"
    skills = profile.get("skills") or {}
    flat_skills = [s for group in skills.values() for s in (group or [])] if isinstance(skills, dict) else list(skills)
    matched = [s for s in item.get("skills") or [] if s.lower() in " ".join(flat_skills).lower()][:3]
    links = "\n".join(filter(None, [profile.get("github"), profile.get("linkedin")]))

    llm = get_llm_client()
    if llm.is_available:
        resume = load_resume_text()[:2500]
        prompt = f"""Write a short email applying for this role.

ROLE: {item.get('title')} at {item.get('company')} ({item.get('location', '')})
POSTING:
\"\"\"
{(item.get('description') or '')[:3500]}
\"\"\"

CANDIDATE: {name}, {profile.get('title', '')}
SKILLS: {', '.join(flat_skills)}
RESUME:
\"\"\"
{resume}
\"\"\"
LINKS: {links}

Rules: under 140 words; greet the team (not a made-up person); one sentence on why this role, two on the most relevant
real experience from the resume; mention the resume is attached; end with the name and links on separate lines.
Never invent projects, numbers, or links. No emojis. Return only the email body."""
        text = llm.generate_text(prompt, system_instruction="You write concise, specific job application emails.", temperature=0.4)
        if text:
            return text.strip()

    skill_line = f"I work with {', '.join(matched)}, which this role asks for. " if matched else ""
    return (
        f"Hi {item.get('company') or 'there'} team,\n\n"
        f"I'm {name}, a {profile.get('title', 'developer')}, and I'd like to apply for the {item.get('title')} role. "
        f"{skill_line}You can see my recent work at {profile.get('github', '')}.\n\n"
        f"My resume is attached. I'm happy to do a short call or a take-home task.\n\n"
        f"Thanks,\n{name}\n{links}"
    )


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
    for old in DATA_DIR.glob("resume_upload.*"):
        old.unlink()
    original_name = "resume.pdf" if lower_name.endswith(".pdf") else f"resume_upload{Path(lower_name).suffix or '.txt'}"
    (DATA_DIR / original_name).write_bytes(file_bytes)

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

    existing_search = load_profile_data().get("search")
    if existing_search:
        profile_yaml_content["search"] = existing_search

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
        first_name = (profile.get("name") or "").split(" ")[0] or "there"
        waiting = [l for l in get_top_leads(limit=100, min_score=7, status="new") if "sample_" not in (l.get("url") or "")]
        if not profile.get("github"):
            return f"Hey {first_name}. Add your GitHub in Profile first, so the drafts can point to your real work."
        if waiting:
            return f"Hey {first_name}. {len(waiting)} {'post is' if len(waiting) == 1 else 'posts are'} waiting for a reply. Open one, or ask me to scan for more."
        return f"Hey {first_name}. Nothing is waiting right now. Want me to scan for new posts?"

    # Intent 0.3: How SARTHI works / what it needs
    if any(q in msg_lower for q in ["what parameters", "what do you need", "how do you work", "what info do you need"]):
        return (
            "I use four things from your profile:\n\n"
            "1. Your resume and skills, to judge fit.\n"
            "2. Your GitHub or LinkedIn, so drafts point to real work.\n"
            f"3. Your rate floor (${profile.get('hourly_rate_usd', 35)}/hr, min ${profile.get('minimum_project_budget_usd', 250)} project), to skip lowball posts.\n"
            "4. Sources: Hacker News hiring threads, plus Reddit once your keys work.\n\n"
            "Update any of these from Profile."
        )

    # Intent 0.4: Agent identity
    if any(q in msg_lower for q in ["who are you", "what is sarthi", "about you"]):
        return (
            "I'm SARTHI. When you run a scan, I read Hacker News hiring threads (and Reddit, once connected), "
            "score each post against your profile, and draft a short reply. You review and send everything yourself."
        )

    # Intent 0.5: Identity / Name Check (Accurate, Direct, No Emojis)
    if any(q in msg_lower for q in ["what is my name", "what's my name", "who am i"]):
        if not profile.get("name"):
            return "I don't know your name yet. Add it in Profile."
        return f"You're {profile['name']}, {profile.get('title', 'developer')}, with your rate set to ${profile.get('hourly_rate_usd', 35)}/hr."

    # Intent 1: Check stats / pipeline
    if any(k in msg_lower for k in ["stats", "tracker", "funnel", "how many leads", "overview"]):
        stats = get_summary_stats()
        leads_str = ", ".join([f"{k.upper()}: {v}" for k, v in stats["leads"].items()]) or "No leads yet"
        jobs_str = ", ".join([f"{k.upper()}: {v}" for k, v in stats["jobs"].items()]) or "No jobs yet"
        return (
            f"Pipeline Overview:\n\n"
            f"- Freelance Leads: {leads_str}\n"
            f"- Internships: {jobs_str}\n\n"
            f"Want me to run a discovery scan now, or review your top matches?"
        )

    # Intent 2: View top leads (real ones only)
    if any(k in msg_lower for k in ["show leads", "top leads", "view leads", "latest leads", "find leads", "show opportunities"]):
        leads = [l for l in get_top_leads(limit=100, min_score=7, status="new") if "sample_" not in (l.get("url") or "")][:5]
        if not leads:
            return "Nothing scoring 7+ is waiting. Ask me to scan for new posts."
        lines = [f"- **{l.get('score')}/10** {l.get('title')}" for l in leads]
        return "Your best open posts:\n\n" + "\n".join(lines)

    # Intent 3: Scan (the web UI runs scans itself via /api/pipeline/run; this path serves the CLI)
    if is_scan_request(msg_lower):
        from main import process_leads
        saved = process_leads(use_sample=False, limit_per_source=15)
        from src.opportunities.refresh import refresh_sources
        report = refresh_sources(manual=True) or {}
        return f"Scan finished. {saved} new HN/Reddit leads and {report.get('new', 0)} new internships and jobs."

    # Intent 4: User pastes a client post to draft a reply
    if "client says" in msg_lower or "post:" in msg_lower or "reply to this" in msg_lower or len(user_msg_clean) > 400:
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
    system_instruction = f"""You are SARTHI, a sharp, elite career and freelance navigator for {profile.get('name', 'the developer')}.
Like a trusted charioteer and strategic guide, you help navigate opportunities with precision and clarity.

Candidate Context:
- Name: {profile.get('name', 'Developer')}
- Rate: ${profile.get('hourly_rate_usd', 35)}/hr
- Skills: {', '.join(profile.get('skills', {}).get('primary', []))}

STRICT RESPONSE RULES:
1. NEVER regurgitate the user's resume back to them unless explicitly asked.
2. NEVER output 4-point numbered lists of what services you can provide for simple questions.
3. Be remarkably concise, human, and direct (1-3 sentences max).
4. Zero emojis in output.
5. If they ask about jobs or freelance work, answer directly and suggest ONE crisp next step.
6. You cannot change settings, send messages, or apply anywhere. For rate, skills, or links, tell them to edit Profile. Never claim you did something you can't do.
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
        "I can't reach Gemini right now, so I can only handle simple commands.\n\n"
        "Try 'scan', 'show top leads', or 'stats'."
    )
