# Project Instructions & UI/UX Standards

This file defines the engineering and design rules for this repository.

## 1. UI/UX & Frontend Standards (Anti-AI Design System)

For any frontend or agent-reply change, strictly follow [UI_UX_RULES.md](UI_UX_RULES.md). In short:

- **Product:** SARTHI is an inbox for paid work: status queue on the left, SARTHI's briefing / post card / conversation on the right. No greeting heroes, prompt chips, or empty dashboard widgets.
- **Honest data first:** never fall back to sample data silently, surface source errors in plain words, never invent links or claim actions that didn't happen.
- **Legal sources only:** public APIs / published datasets that allow automated access (see `src/opportunities/sources.py`). Never scrape LinkedIn, Internshala, Naukri, Wellfound or Indeed, and respect robots.txt for any HTML page (company careers engine in `src/opportunities/companies.py`). Their jobs may only arrive via the user's own job-alert emails, read-only over IMAP (`src/opportunities/email_alerts.py`); never fetch the linked pages.
- **Email only on click:** `/api/outreach/send` is the only path that sends mail; never call it from the scheduler or agent.
- **Color:** black and white neutrals (`#0b0b0c`, `#111113`, `#17171a`, white at 64% / 40% opacity for hierarchy) and ONE accent, ember `#ff5722`, used only for the mark, primary button, selection, and focus. No gradients, glows, or extra colors.
- **No emojis** in the UI or in SARTHI's replies. Lucide SVG icons only.
- **Tabular monospace:** `JetBrains Mono` with `tabular-nums` for scores, money, counts, and times.
- **Motion:** the chakra mark spins only while SARTHI is working.

## 2. Engineering Behavior Guidelines

- **Simplicity First:** Write minimal, surgical code. No speculative abstractions or unrequested features.
- **Goal-Driven Execution:** Define clear success criteria and verify with tests or runtime execution.
- **Preserve System Architecture:** Respect FastAPI backend routes and database schemas in `src/core/db.py`.
