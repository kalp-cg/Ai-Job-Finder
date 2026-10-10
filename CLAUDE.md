# Project Instructions & UI/UX Standards

This file defines the engineering and design rules for this repository.

## 1. UI/UX & Frontend Standards (Anti-AI Design System)

For any frontend code (HTML, CSS, JS, React, etc.), strictly follow [UI_UX_RULES.md](file:///home/kalppatel/Desktop/job-ai-project/UI_UX_RULES.md):

- **Strict NO-EMOJI Rule in UI Chrome:** Never use OS emojis (`⚡`, `🔍`, `🚀`, `💬`, `🎯`) for icons, buttons, or navigation. Always use **Lucide SVG icons** (`<i data-lucide="..."></i>` with `lucide.createIcons()`).
- **No Chatbot-First Screen Layouts:** Functional tools must NOT default to centered "ChatGPT/Claude" greeting heroes with prompt pills. Always build a **High-Density Operator Split-Pane** (telemetry header, dense feed on the left, active inspector/action studio on the right).
- **Matte Earthen Palette & The 90/9/1 Rule:** Ground surfaces with warm espresso neutrals (`#161513`, `#1D1C19`, `#24231F`) and `#292823` hairline borders. Never use the primary-color "traffic light" rainbow (blue buttons, green badges, yellow tags). Use linen text opacity for 99% of UI hierarchy and strictly ONE signature accent (Terracotta `#D97757`).
- **Tabular Monospace Numerics:** Use `JetBrains Mono` with `tabular-nums` for all rates, match scores, timestamps, and badges.

## 2. Engineering Behavior Guidelines

- **Simplicity First:** Write minimal, surgical code. No speculative abstractions or unrequested features.
- **Goal-Driven Execution:** Define clear success criteria and verify with tests or runtime execution.
- **Preserve System Architecture:** Respect FastAPI backend routes and database schemas in `src/core/db.py`.
