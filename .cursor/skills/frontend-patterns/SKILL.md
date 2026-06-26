---
name: frontend-patterns
description: >-
  Zivo frontend patterns. Pointer skill — the canonical UI rules are in
  AGENTS.md (Frontend UI / Calm Paper) and docs/CONVENTIONS.md. Use when
  building React/Next.js components, state, data fetching, or forms.
---

# Frontend Patterns — see the canonical UI rules

The UI rules for this repo are documented once and must not be forked here:

- **[AGENTS.md → Frontend UI](../../../AGENTS.md#frontend-ui)** — Mantine-only, no
  custom components, where UI lives, allowed imports, and the full **Calm Paper** token
  system (typography, color, shadows, radii, motion). This is the source of truth.
- **[docs/CONVENTIONS.md → Frontend](../../../docs/CONVENTIONS.md#3-frontend-nextjs-app-router--mantine)** — the one-paragraph summary + `file:line` proof.
- **[ADR 0003](../../../docs/adr/0003-mantine-only-calm-paper.md)** — *why* Mantine-only.
- Use the **`design-system`** skill for visual-system work.

## What this repo actually does (do not import another project's patterns)

- **Build from Mantine 9 components**, themed via `createTheme` in
  `frontend/app/providers.tsx`. **No** custom component library, **no** Tailwind, **no**
  shadcn, **no** hand-rolled CSS files, **no** `framer-motion` for layout chrome.
- Pages, layouts, and collocated `_components/` live under **`frontend/app/`**.
  Non-UI client code (API helpers, types) lives in `frontend/lib/`.
- Style with Mantine props and theme tokens (`c="lavender.7"`, `bg="gray.0"`,
  `shadow="paper"`, `radius="xl"`) — never hardcoded hex. Inline `style={}` only for
  genuinely dynamic/animated values.
- Respect `prefers-reduced-motion`; transitions use the Calm Paper easing
  (`cubic-bezier(0.32, 0.72, 0, 1)`, ~280ms).

For App Router mechanics (Route Handlers, Server Components, streaming, metadata),
keep heavy logic and provider keys on the server; validate inputs before mutations.
Anything not covered here lives in the canonical docs above — read those, don't guess.
