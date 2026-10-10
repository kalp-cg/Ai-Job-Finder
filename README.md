# SARTHI — AI Career & Opportunity Navigator

An autonomous engineering copilot and navigator that monitors public developer job requests and internships across Hacker News & Reddit, scores them against your profile, and drafts tailored, high-converting replies. **You review and send everything by hand.**

> [!IMPORTANT]
> **Safety Boundary:** This tool is strictly read-only. It **never** posts, comments, DMs, applies, or emails anyone automatically. All drafts are prepared for your manual review to keep your accounts secure and ensure thoughtful replies.

---

## 1. Architecture Overview

The system consists of two discovery modules sharing a common core:

```
job-assistant/
├── .env.example            # Environment templates (GEMINI_API_KEY, REDDIT keys)
├── .gitignore              # Ignores credentials, SQLite databases, and caches
├── requirements.txt        # Dependencies (google-genai, pydantic, praw, rich, etc.)
├── config/
│   ├── profile.yaml        # Skills, rates, city, and minimum stipend
│   ├── queries.yaml        # Internship keywords and boards
│   └── leads.yaml          # Subreddits, HN queries, keywords, and blocked terms
├── data/
│   ├── resume.md           # Your experience summary
│   ├── projects.md         # Highlighted projects with links (3-4 lines each)
│   ├── portfolio.md        # Freelance case studies and proof-of-work URLs
│   └── assistant.db        # SQLite database: leads, jobs, and status tracking
├── src/
│   ├── core/
│   │   ├── llm.py          # Gemini 2.5 wrapper with Pydantic JSON schemas
│   │   ├── db.py           # SQLite migrations, deduplication, and status updater
│   │   └── notifier.py     # Terminal Rich tables and Markdown daily digest
│   ├── jobs/
│   │   ├── collector.py    # Public job page collector (Lever, Greenhouse, Ashby)
│   │   ├── extractor.py    # Gemini job role extraction
│   │   ├── matcher.py      # Profile & stipend scoring (0-10)
│   │   └── drafter.py      # Application intro note generator
│   └── leads/
│       ├── reddit_source.py # PRAW official read-only Reddit scraper (<48h)
│       ├── hn_source.py     # Hacker News official Algolia API (<48h)
│       ├── extractor.py    # Gemini post classifier & JSON extractor (pydantic)
│       ├── scorer.py       # Fit evaluation (0-10, keeps >= 7)
│       └── drafter.py      # Tailored reply drafts (<100 words, 4 parts)
└── main.py                 # CLI controller: run, leads, jobs, digest, track
```

---

## 2. Core Modules

### Module A: Internship Finder (`src/jobs/`)
- **Collector:** Monitors public job pages across Greenhouse, Lever, Ashby, and Work at a Startup.
- **Extractor:** Extracts role details, compensation/stipend, location, and requirements using Gemini into a validated `JobExtraction` Pydantic model.
- **Matcher:** Scores roles (0-10) against `config/profile.yaml` skills and stipend minimums.
- **Drafter:** Writes a concise intro note linking to relevant projects from `data/projects.md`.

### Module B: Freelance Lead Finder (`src/leads/`)
- **Reddit Source:** Uses official **PRAW** in read-only mode to scan high-intent subreddits (`r/forhire`, `r/webdev`, `r/startups`, `r/SideProject`). Filters out `[For Hire]` and spam.
- **Hacker News Source:** Queries the official **Algolia Search API** for recent "SEEKING FREELANCER" posts.
- **Extractor:** Classifies whether a post is a paid work request and extracts `need`, `budget`, `timeline`, `tech`, `contact_method`, and `url`.
- **Scorer:** Scores candidates (0 to 10) against your skills, portfolio, budget realism, and clarity. Drops any lead below 7.
- **Drafter:** Crafts a reply strictly under 100 words following the 4-part conversion framework:
  1. Acknowledge and understand their specific problem.
  2. One actionable suggestion or technical question.
  3. One relevant proof-of-work link from `data/portfolio.md`.
  4. Clear, friction-free next step ("Happy to share a quick plan...").

### Shared Core (`src/core/`)
- **`llm.py`:** Wrapper around the official Google GenAI SDK (`google-genai`). Enforces structured output parsing with fallback resilience.
- **`db.py`:** SQLite database storing leads and opportunities. Blocks duplicate URLs and duplicate authors.
- **`notifier.py`:** Formats clean Markdown digests in `data/digest_YYYY-MM-DD.md` and displays terminal tables.

---

## 3. Quickstart & Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure credentials
Copy the environment template:
```bash
cp .env.example .env
```
Edit `.env` and fill in:
- `GEMINI_API_KEY`: Get a free key at [Google AI Studio](https://aistudio.google.com/app/apikey).
- `REDDIT_CLIENT_ID` & `REDDIT_CLIENT_SECRET`: Create a free script app at [Reddit App Preferences](https://www.reddit.com/prefs/apps).

*(Note: The assistant includes offline heuristic fallbacks and built-in sample datasets, so you can test and run it immediately even before adding API keys).*

---

## 4. Interactive Interfaces & Usage Commands

### Launch Claude-Style Web App (Browser UI)
Starts the local web server with chat, profile onboarding, pre-set questions, and copyable drafts:
```bash
python3 main.py web
```
Open your browser at **http://localhost:8000**.

### Launch Interactive Terminal Chat (Claude Mode)
Talk directly to your Job Assistant in the terminal:
```bash
python3 main.py chat
```

---

### Command Line Tools

### Run Full Pipeline
Runs lead discovery, internship matching, deduplication, and prints the daily digest:
```bash
python3 main.py run
```
Or run with sample test data:
```bash
python3 main.py run --sample
```

### Run Freelance Leads Only
```bash
python3 main.py leads
```

### View Daily Digest
Display top leads and review the draft replies:
```bash
python3 main.py digest --limit 10
```

### Track Outreach Status
View high-level funnel stats:
```bash
python3 main.py track
```

Update status when you contact someone:
```bash
python3 main.py track --lead-id 1 --status contacted --notes "Sent DM with portfolio link"
python3 main.py track --lead-id 1 --status replied
python3 main.py track --lead-id 1 --status won
```

---

## 5. Daily Routine (15 Minutes)

1. **Morning Run:** Run `python3 main.py run`.
2. **Review Digest:** Open `data/digest_<today>.md` or view the terminal output.
3. **Inspect Top 5 Leads:** Click the post URL to verify it's still open.
4. **Copy & Customize Draft:** Adapt the under-100-word draft with any extra nuance.
5. **Send by Hand:** Reply via Reddit PM/comment or client email.
6. **Update Tracker:** Run `python3 main.py track --lead-id <ID> --status contacted`.
