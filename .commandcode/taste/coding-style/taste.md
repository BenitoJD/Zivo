# Coding Style Preferences

- Monorepo with `backend/`, `frontend/`, `infra/k8s/` directories. Confidence: 0.95
- Backend: Python with type hints, ruff configured for E9+F gate only, raw parameterized SQL (not ORM). Confidence: 0.9
- Frontend: TypeScript with Next.js 16 App Router, Mantine 9 components. CSS via Mantine's style system. Confidence: 0.95
- "Calm Paper" design tokens: warm paper bg, lavender/sage/terracotta/forest palette, serif display font, ink buttons. Mantine-only component rule. Confidence: 0.9
- npm (not pnpm, not yarn). Confidence: 0.85
- Avoid large monolithic files: refactored page.tsx from 5,243 lines to ~1,415 by extracting modules. Duplication (WaitState, mode unions) must be consolidated. Confidence: 0.9
- Tolerant JSON parsing: fence-slicing + salvage on truncated LLM output, never crash on a malformed response. Confidence: 0.85
- Find root causes, never apply patch fixes. This is explicitly stated and repeatedly enforced. Confidence: 0.98
- No workarounds. User says "please don't do any workaround, find and fix the root cause." Confidence: 0.98
- Prefers clean codebase: remove stale/dead code, no unused imports, proper cleanup when replacing features (e.g., remove coderunner when Judge0 is adopted). Confidence: 0.9
- Test isolation matters: use autouse fixtures to clear caches that get poisoned across test modules. Confidence: 0.8
- Prompt engineering as a quality lever: write generation prompts that produce passing output on the first try rather than relying on retry loops. Confidence: 0.9
- No character/explanation length limits in the UI — show the full content the LLM produced. Confidence: 0.85
- Shared `require_document` / `require_ready_document` guards in the API layer, not duplicated per-endpoint. Confidence: 0.8
