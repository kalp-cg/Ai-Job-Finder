# SARTHI Product & Design Rules

These rules describe what SARTHI is and how its interface is built. They exist so every future change, by a person or an AI agent, moves in the same direction instead of restyling the app again.

---

## 1. What SARTHI is

SARTHI is an **inbox for paid work**. Every day it should let you do one loop in about 15 minutes:

> find posts that fit you → read why they fit → edit the draft → send it yourself → track what happened

Everything in the UI serves that loop. If a change doesn't make the loop faster, clearer, or more trustworthy, it doesn't ship.

SARTHI never posts, applies, or messages anyone on its own. An application email goes out only when you click Send, after a 5-second undo, within a daily cap. Auto-scan only reads.

---

## 2. Honesty rules (most important)

A beautiful UI on fake data feels hollow. These come before any visual rule.

1. **No silent sample data.** Live scans never fall back to sample posts. Sample posts in the database are labelled `SAMPLE` and hidden by default.
2. **Say what's broken.** If a source fails (for example, Reddit keys rejected), the top bar and the briefing say so in plain words.
3. **Never claim what didn't happen.** SARTHI only says "scanned" after a scan, gives real counts, and never says it changed a setting it can't change.
4. **Only real contact points.** An address counts as "apply here" only if the text around it is about applying; boilerplate HR, privacy and accommodation addresses never become send targets.
5. **Drafts only cite real work.** Proof-of-work links come from `config/profile.yaml`. No placeholder or invented links.
6. **No fake loading copy.** Thinking states describe the real step ("Reading the post") and show elapsed time. No "Synthesizing strategic intelligence...".

---

## 3. Layout

```
┌───────────────────────────────────────────────────────────────┐
│ SARTHI  Last scan 6m ago · Hacker News · Reddit off  [Scan] [You] │
├────────────────────┬──────────────────────────────────────────┤
│ New  Sent  Won  Passed │  SARTHI briefing (real counts, problems) │
│ 8/10  Role · Company   │  Top 3 waiting posts                     │
│ 8/10  Role · Company   │                                          │
│                        │  -- or, with a post open --              │
│                        │  Post card: meta, why it fits, the post, │
│                        │  editable reply, actions                 │
│ j/k move · / ask       │  Conversation about this post            │
│                        │  [ Ask SARTHI ...                    ↑ ] │
└────────────────────┴──────────────────────────────────────────┘
```

- **Left, the queue:** kind filter (All, Internships, Jobs, Freelance), tabs by status (New, Sent, Won, Passed), one compact row per post. Always visible.
- **Right, the thread:** SARTHI speaks first with a short briefing built from real data. Opening a post shows its card, and the conversation below is about that post.
- **No greeting hero, no prompt chips, no empty dashboard widgets.** If there's nothing to show, say so in one sentence and offer the next action.
- **Inbox behaviour:** when you mark a post sent or pass on it, the next post in the tab opens.
- **Profile and Settings are pages, not modals.** Profile: resume dropzone, what you're looking for, about you. Settings: auto-scan interval, sources with their last run, email setup, alerts.
- **Search answers live in the thread:** the parsed filters as chips, ranked results as rows, and an "Alert me" action.

---

## 4. Visual system

### Color: black and white, one ember accent

```css
--bg: #0b0b0c;        --surface: #111113;   --raised: #17171a;
--line: rgba(255,255,255,.07);   --line-strong: rgba(255,255,255,.13);
--text: #ededef;      --text-2: 64% opacity;   --text-3: 40% opacity;
--accent: #ff5722;    /* the only saturated color */
```

- Hierarchy comes from **opacity and weight**, never from extra colors.
- The ember accent appears in exactly four places: the SARTHI mark, the primary button, the selected-post marker, and focus/draw lines. Nowhere else.
- No gradients, glows, glassmorphism, or traffic-light colors (green success, yellow warning, blue links).

### Type
- **Plus Jakarta Sans** for UI and reading.
- **JetBrains Mono** with `tabular-nums` for scores, money, counts, and times.
- Section labels are small uppercase, 40% opacity. Don't uppercase user content such as URLs or emails.

### Icons
- **Lucide only** (`<i data-lucide="...">`), 15px, stroke 1.8. **No emojis** anywhere in the UI or in SARTHI's replies.

### The mark
- The SARTHI chakra is a single flat SVG symbol (`#chakra` in `index.html`), drawn in the accent color.
- It is still when idle and **spins only while SARTHI is working**. Motion means "something is happening", nothing else.

### Motion
- Messages rise in over 0.28s. The input draws an ember line from the centre on focus. Buttons scale to 0.98 on press.
- `prefers-reduced-motion` turns all of it off.

---

## 5. How SARTHI talks

- 1 to 3 sentences. First name only. No resume recitals, no numbered lists of capabilities.
- Write to the user as "you", never "the developer" or "the candidate".
- Always end with one concrete next step, never a menu of options.

---

## 6. Prompt block for AI agents

```markdown
SARTHI DESIGN DIRECTIVES
1. SARTHI is an inbox for paid work: queue on the left, SARTHI thread and post card on the right. Do not build chat-first heroes, prompt chips, or dashboard widgets without real data.
2. Honest data first: no silent sample fallbacks, surface source errors in plain words, never invent links or claim actions that didn't happen.
3. Black and white with ONE accent (#ff5722) used only for the mark, primary button, selection, and focus. No gradients, glows, or extra colors.
4. Lucide SVG icons only. Zero emojis in UI and in SARTHI's replies.
5. JetBrains Mono + tabular-nums for every score, amount, count, and time.
6. The chakra mark spins only while work is in progress.
7. SARTHI replies in 1-3 sentences, addresses the user as "you", ends with one next step.
```
