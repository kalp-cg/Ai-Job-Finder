# 🎨 Anti-AI UI/UX Design System & Rulebook
### *The Senior Product Designer's Standard for AI-Assisted Code*

> **Purpose:** Drop this rulebook into any project as a system prompt, workspace standard (`CLAUDE.md` / `.cursorrules` / `.agents/rules/`), or team design spec. It permanently eliminates the generic "AI-slop" aesthetic and forces models to design like high-level product designers (Claude, Linear, Stripe, Raycast).

---

## 💀 The Dead Reality of AI Design (The 5 Anti-Patterns)

| What AI Generates by Default | Why AI Does It | What Senior Designers Demand |
| :--- | :--- | :--- |
| **1. The "Traffic Light" Color Rainbow** (Blue button, green badge, yellow warning, red tag) | **GitHub Averaging:** 80% of open-source repos use default Tailwind utility classes (`bg-blue-600`, `text-green-500`). AI regurgitates the statistical average. | **The 90 / 9 / 1 Rule:** 90% unified neutrals, 9% typographic opacity hierarchy, **<1% single signature accent color**. |
| **2. Cold Sterile Tech Slates (`#0f172a`, `#1e293b`)** | AI defaults to cold blue-gray dark modes from generic developer dashboards. | **Intentional Color Temperature:** Warm espresso/earthen undertones (`#1b1a18`, `#22211e`) or pure obsidian (`#08090a`). |
| **3. UI Emojis (`⚡`, `🔍`, `🚀`, `💬`, `🎯`)** | Lazy shortcut to avoid writing SVG markup. Renders like a cartoon on Windows/Linux. | **Strictly 100% Vector SVGs** (Lucide, Radix, Phosphor). Zero OS emojis in chrome, buttons, or badges. |
| **4. The ChatGPT/Claude Hero Greeting** | Memorized training bias: "Good day, [User]" + sparkle (`✦`) + 4 prompt chips. | **Operator Workspace First:** Start immediately on domain telemetry, live queues, and high-density data. |
| **5. Bloated Padding & Low Density** | 40px spacers to simulate minimalism without real UI structure. | **Compact Operator Density:** Tabular numbers (`JetBrains Mono`), tight 1px borders, data-rich split-pane workflows. |

---

## 🎨 Masterclass: The Senior Designer Palette Architecture

Senior product designers and design directors at Anthropic, Linear, or Stripe will **instantly reject** multi-colored dashboards. 

> **Core Law:** *"If everything has color, nothing has power."*

### 1. The 90 / 9 / 1 Rule
1. **90% Neutrals (The Foundation):**
   - Pick **one color temperature** for your neutrals (e.g. Warm Espresso or Deep Obsidian).
   - All backgrounds, cards, borders, and modal surfaces must stay strictly inside this neutral family.
2. **9% Informational Hierarchy via Opacity:**
   - Do NOT use color to indicate secondary info. Use **font weight and opacity**:
     - Primary Headings & Data: `100%` White / Warm Linen (`#F5F4EF`)
     - Body & Descriptions: `65%` Muted (`#A8A69E`)
     - Micro-labels & Dividers: `35–45%` Dim (`#6E6C64`)
3. **1% Single Signature Accent:**
   - Exactly **ONE** signature hue on the entire screen (e.g. Claude Terracotta `#D97757` or Linear Indigo `#5E6AD2`).
   - Use it exclusively for brand identity, primary focus states, and the single most critical action.

---

### 2. The Claude Editorial Palette Specification (Warm Earth & Terracotta)

Used by Anthropic’s Claude.ai to create a literary, bespoke, editorial feel that avoids cold tech clichés:

```css
:root {
  /* Warm Earthen Surfaces (90%) */
  --bg-base: #181715;         /* Deep warm espresso canvas */
  --bg-surface: #201f1c;      /* Navbars, sidebars, panels */
  --bg-elevated: #282723;     /* Cards, dialogs, inputs */
  --bg-hover: #32302b;        /* Hover & active states */

  /* Structural Hairline Borders */
  --border-subtle: #2e2c27;
  --border-default: #3d3a34;
  --border-focus: #d97757;

  /* Typography Hierarchy (9%) */
  --text-primary: #f5f4ef;    /* 100% - Warm unbleached linen */
  --text-secondary: #a8a69e;  /* 65%  - Muted stone */
  --text-muted: #6e6c64;      /* 40%  - Subtle metadata */

  /* The 1% Signature Accent */
  --accent-signature: #d97757; /* Warm Terracotta Clay */
  --accent-signature-hover: #e28a6c;
  --accent-signature-bg: rgba(217, 119, 87, 0.12);

  /* Restrained Functional Status (Muted, Not Neon) */
  --status-active: #34d399;   /* Muted Sage */
  --status-active-bg: rgba(52, 211, 153, 0.10);
  --status-value: #e5b869;    /* Muted Ochre for financial/rates */

  /* Typography */
  --font-sans: 'Inter', -apple-system, sans-serif;
  --font-mono: 'JetBrains Mono', monospace;
}
```

---

## 🛠️ Topic-Wise Implementation Rules

### Topic 1: Iconography (Zero Emojis in UI Chrome)
- Never use emojis (`⚡`, `🔍`, `🗑️`, `⚙️`) as interface icons. Emojis render cartoonishly on Windows and inconsistently on Linux.
- Use **Lucide SVG icons** (`<i data-lucide="..."></i>`) with:
  - Standard size: `15px` to `18px`.
  - Consistent stroke width: `1.75px`.
  - Inherit color: `stroke="currentColor"`.

### Topic 2: Layout (Operator Split-Pane vs. Chatbot Void)
- When building functional tools (scrapers, trackers, admin desks, analytics), **reject the centered chat void**.
- **Top Telemetry Bar:** Real-time counters, active sources, system pulse.
- **Left/Center Feed (50%):** High-density list with search, filters, tabular rates, and compact tags.
- **Right Inspector (50%):** Immediate deep-dive, editable AI outputs, 1-click clipboard actions, and status controls.
- Chat/AI should be a command drawer or secondary console tab—never the entire screen.

### Topic 3: Typography & Numbers
- **Tabular Monospace:** Every monetary figure (`$35/hr`), score (`9.4/10`), count, and timestamp MUST use monospace (`JetBrains Mono`) with `font-variant-numeric: tabular-nums` to guarantee vertical alignment.
- **Case & Tracking:** Use uppercase micro-headers (`letter-spacing: 0.05em; font-size: 0.68rem; font-weight: 600; color: var(--text-muted)`).

### Topic 4: Micro-Interactions
- Avoid heavy neon drop shadows or fuzzy blur glows.
- Use hairline border brightening on hover (`border-color: var(--border-default)`).
- Provide immediate tactile feedback on copy actions (e.g., `copy` icon turns into `check` for 2 seconds with a subtle success pill).

---

## 📋 The "Copy-Paste" Prompt for Any AI Agent

Include this block in your prompt to enforce senior designer standards on any LLM:

```markdown
STRICT FRONTEND DESIGN DIRECTIVES:
1. Zero OS emojis in interface controls, buttons, or navigation. Use Lucide SVG icons exclusively.
2. Color Restraint (90/9/1 Rule): Reject the primary-color rainbow (blue buttons, green badges, yellow tags). Use warm espresso/linen neutrals for 99% of the UI, and exactly ONE signature accent color (Terracotta #d97757). Create contrast via font weight and opacity, not rainbow colors.
3. No centered ChatGPT/Claude sparkle greeting heroes ("Good day Developer"). Build a dense Operator Workspace with a top telemetry bar and split-pane layout (data feed on left, inspector/action studio on right).
4. Tabular Numbers: Use JetBrains Mono with tabular-nums for all prices, scores, and timestamps.
```
