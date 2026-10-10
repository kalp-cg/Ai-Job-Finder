"""
FastAPI application serving the Claude-style Job Assistant web interface and APIs.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.core.db import get_summary_stats, get_top_jobs, get_top_leads, update_lead_status
from src.web.agent import (
    get_suggested_questions,
    handle_user_message,
    load_portfolio_text,
    load_profile_data,
    load_resume_text,
    onboard_user_profile,
    parse_resume_file,
)

app = FastAPI(title="Job + Freelance AI Assistant", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ChatRequest(BaseModel):
    message: str
    history: Optional[List[Dict[str, str]]] = []


class OnboardRequest(BaseModel):
    name: str
    skills: str
    github_url: str
    linkedin_url: Optional[str] = ""
    resume_or_bio: str
    hourly_rate: Optional[int] = 35
    min_budget: Optional[int] = 250


class UpdateStatusRequest(BaseModel):
    status: str
    notes: Optional[str] = None


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Index HTML not found")
    return FileResponse(index_file)


@app.get("/api/suggested-questions")
async def get_questions():
    return {"questions": get_suggested_questions()}


@app.get("/api/profile")
async def get_profile():
    profile = load_profile_data()
    resume = load_resume_text()
    portfolio = load_portfolio_text()
    return {
        "profile": profile,
        "has_resume": bool(resume),
        "has_portfolio": bool(portfolio),
        "configured": bool(profile.get("github") or profile.get("skills")),
    }


@app.post("/api/profile/upload-resume")
async def upload_resume(file: UploadFile = File(...)):
    content = await file.read()
    filename = file.filename or "resume.pdf"
    parsed = parse_resume_file(content, filename)
    return parsed


@app.post("/api/onboard")
def onboard_profile(req: OnboardRequest):
    result = onboard_user_profile(
        name=req.name,
        skills_input=req.skills,
        github_url=req.github_url,
        linkedin_url=req.linkedin_url or "",
        resume_or_bio=req.resume_or_bio,
        hourly_rate=req.hourly_rate or 35,
        min_budget=req.min_budget or 250,
    )
    return result


@app.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    reply = handle_user_message(req.message, req.history)
    return {"reply": reply}


@app.get("/api/leads")
def list_leads(limit: int = 20, min_score: int = 7):
    leads = get_top_leads(limit=limit, min_score=min_score)
    return {"leads": leads}


@app.post("/api/leads/{lead_id}/status")
def update_status(lead_id: int, req: UpdateStatusRequest):
    success = update_lead_status(lead_id, req.status, notes=req.notes)
    if not success:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"status": "success", "lead_id": lead_id, "new_status": req.status}


@app.get("/api/stats")
def stats():
    return get_summary_stats()


@app.post("/api/pipeline/run")
def run_pipeline():
    from main import process_jobs, process_leads
    new_leads = process_leads(use_sample=False, limit_per_source=15)
    new_jobs = process_jobs(use_sample=True)
    return {
        "status": "success",
        "new_leads_count": new_leads,
        "new_jobs_count": new_jobs,
    }
