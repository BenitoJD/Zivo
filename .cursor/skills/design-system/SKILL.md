---
name: "design-system"
description: "Generate or audit visual systems, design tokens, and UI consistency across a codebase."
---

# Design System — Generate & Audit Visual Systems

## When to Use

- Starting a new project that needs a design system
- Auditing an existing codebase for visual consistency
- Before a redesign — understand what you have
- When the UI looks "off" but you can't pinpoint why
- Reviewing PRs that touch styling

## Repo Note

**This repo already has a design system: "Calm Paper".** It is documented in
[AGENTS.md → Frontend UI](../../../AGENTS.md#frontend-ui) and locked in by
[ADR 0003](../../../docs/adr/0003-mantine-only-calm-paper.md). Use this skill to
*audit against* that system, not to invent a new one.

- The token source of truth is `frontend/app/providers.tsx` (Mantine `createTheme`):
  serif/sans fonts, lavender accent, sage/terracotta feedback, `shadow="paper"`, pill
  radii. Audit drift *from these tokens* (hardcoded hex, non-Mantine components).
- UI lives in `frontend/app/`; auth is handled by the `/login` route
  and `frontend/lib/auth.ts` (there is no `AuthWrapper`).
- The product surfaces are the Learn/Test **workspace** and the MCQ study cards —
  keep them consistent with each other.
- **Do not introduce a new design system or token layer** (ADR 0003) unless the user
  explicitly asks for a redesign.

## How It Works

### Mode 1: Generate Design System

Analyzes your codebase and generates a cohesive design system:

```
1. Scan CSS/Tailwind/styled-components for existing patterns
2. Extract: colors, typography, spacing, border-radius, shadows, breakpoints
3. Research 3 competitor sites for inspiration (via browser MCP)
4. Propose a design token set (JSON + CSS custom properties)
5. Write proposed tokens and rationale in the existing design/docs
   location if one exists; otherwise ask before creating a new top-level doc
6. Create a preview artifact only when the user asks for one
```

Output: repo-appropriate design notes plus any requested token or preview
artifacts

### Mode 2: Visual Audit

Scores your UI across 10 dimensions (0-10 each):

```
1. Color consistency — are you using your palette or random hex values?
2. Typography hierarchy — clear h1 > h2 > h3 > body > caption?
3. Spacing rhythm — consistent scale (4px/8px/16px) or arbitrary?
4. Component consistency — do similar elements look similar?
5. Responsive behavior — fluid or broken at breakpoints?
6. Dark mode — complete or half-done?
7. Animation — purposeful or gratuitous?
8. Accessibility — contrast ratios, focus states, touch targets
9. Information density — cluttered or clean?
10. Polish — hover states, transitions, loading states, empty states
```

Each dimension gets a score, specific examples, and a fix with exact
file:line where possible.

### Mode 3: AI Slop Detection

Identifies generic AI-generated design patterns:

```
- Gratuitous gradients on everything
- Purple-to-blue defaults
- "Glass morphism" cards with no purpose
- Rounded corners on things that shouldn't be rounded
- Excessive animations on scroll
- Generic hero with centered text over stock gradient
- Sans-serif font stack with no personality
```

## Preferred use in this repo

- Audit existing Mantine/Calm-Paper UI for drift before proposing anything new
- Keep recommendations expressed as Mantine theme tokens, never new CSS layers
- Treat the workspace shell, the MCQ study card, and the landing page as the
  highest-value consistency surfaces

## Examples

**Generate for a SaaS app:**

```
/design-system generate --style minimal --palette earth-tones
```

**Audit existing UI:**

```
/design-system audit --url http://localhost:3000 --pages / /pricing /docs
```

**Check for AI slop:**

```
/design-system slop-check
```
