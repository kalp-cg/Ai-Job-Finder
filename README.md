# SARTHI — AI Career & Opportunity Navigator

An inbox for paid work. SARTHI pulls internships, jobs and freelance posts from public feeds, ranks them against your profile, drafts your application, and sends it by email in one click when the post accepts email.

> [!IMPORTANT]
> **Safety Boundary:** SARTHI never posts, comments, DMs or applies on its own. An email goes out only when you click **Send email**, after a 5-second undo window, with a daily cap (default 20) and no repeat sends to the same address for the same post. Auto-scan only reads feeds; it never sends anything.

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

### Module A: Opportunity Feeds (`src/opportunities/`)
Public APIs and published datasets only. Each source's terms (credit the source, link to the original, modest request rates) are respected.

| Source | What it gives | Min gap between calls |
| :--- | :--- | :--- |
| SimplifyJobs (GitHub dataset) | ~4-5k active tech internships, mostly US | 1 h |
| The Muse API | Internships and entry-level roles, incl. India and remote | 1 h |
| Unstop public API | Indian internships with stipend and deadline | 1 h |
| Remotive API | Remote dev jobs (max ~4 calls/day by their terms) | 6 h |
| Remote OK API | Remote jobs with salary ranges | 2 h |
| Jobicy API | Remote engineering jobs | 2 h |
| Himalayas API | Remote jobs with location limits and salaries | 2 h |

**Not included on purpose:** LinkedIn, Internshala, Naukri, Wellfound and Indeed. Their terms or robots.txt forbid automated access.

- **`normalize.py`:** one shape for every posting. Parses skills (with synonyms), pay in any currency/period to monthly USD, experience asked, remote, and an application email only when the text around it is about applying (HR-accommodation and privacy addresses are ignored).
- **`ranking.py`:** no LLM calls, so thousands of postings rank in ~50 ms. Score = 45% relevance (skill coverage + BM25), 15% pay fit, 15% location fit, 15% freshness (10-day half-life), 10% experience fit. Hard filters for kind, location, closed/expired, and unpaid when you ask for pay. Cross-source duplicates collapse.
- **`refresh.py`:** fetches due sources, stores them, marks postings that disappear as closed, updates alerts.
- **`mailer.py`:** sends one email via your SMTP account (Gmail App Password), with your resume attached.

### Module B: Freelance Lead Finder (`src/leads/`)
- **Reddit Source:** Uses official **PRAW** in read-only mode to scan high-intent subreddits (`r/forhire`, `r/webdev`, `r/startups`, `r/SideProject`). Filters out `[For Hire]` and spam.
- **Hacker News Source:** Reads this month's "Ask HN: Who is hiring?" and "Freelancer? Seeking freelancer?" threads through the official **Algolia API**, keeps remote posts, and ranks them by overlap with your skills before any LLM call.
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

*(Note: Without a Gemini key, scoring and drafts fall back to simple rules. Sample posts are only used with `--sample`; live scans never mix them in, and the web app labels and hides any sample posts already in the database.)*

---

## 4. Interactive Interfaces & Usage Commands

### Launch the Web App
Starts the local web app: a queue of posts by status (New, Sent, Won, Passed), each post with why it fits and an editable reply, and a conversation with SARTHI about the open post. Shortcuts: `j`/`k` move through the queue, `/` focuses the input, `Esc` returns to the overview.
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

1. **Open SARTHI** (`python3 main.py web`). With auto-scan on (Settings), the queue is already fresh.
2. **Ask for what you want**, e.g. "React internship, 1 year experience, ₹15k stipend". Save it as an alert to hear about new matches.
3. **Open a post**, read why it fits, click **Write my application**, edit it.
4. **Send email** when the post takes email; otherwise **Copy & open** and paste into their form.
5. **Mark applied**, then move it along (Interviewing, Offer, Rejected) as replies come in.
