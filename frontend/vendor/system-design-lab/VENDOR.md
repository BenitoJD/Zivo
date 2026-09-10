# Design Lab (vendored engine)

The System Design simulation lab at `/practice/system-design/lab`: place
components on a canvas, wire them, raise traffic, and watch queueing, tail
latency, retry storms and circuit breakers emerge from a real discrete-event
simulation. 33 components, 23 worked scenarios (including reconstructions of
well-known production architectures), chaos knobs, deterministic seeds and a
built-in glossary. Runs entirely client-side; designs persist to localStorage.

- **License:** the engine ships under the MIT license — see `LICENSE` in this
  directory, which must stay intact in any redistribution.
- **Snapshot:** the source in `src/` is frozen at the integration snapshot
  (2026-09-10); edits to it are limited to the adaptations listed below.

## Layout

- `src/**` — the engine and its canvas UI (imports resolve from Zivo's
  frontend; the mount point is
  `frontend/app/(shell)/practice/system-design/lab/_components/DesignLab.tsx`).
  The Vite entry (`main.tsx`) is not carried; the Vitest specs ride along
  for reference only (Zivo's frontend has no Vitest runner — run them against
  the source checkout with `bun test` when judging fidelity).
- `scope-css.py` — see below.
- The Caveat webfont the CSS loads lives at
  `frontend/public/fonts/Caveat/` (SIL OFL 1.1, licence file included).

## Why vendored, not ported

The engine's entire value is simulation fidelity ("the numbers have to be
true"): porting it to Mantine and the zero-if rule-table style would fork the
physics and turn every fix into a manual re-derivation. A simulator is an
instrument, not a policy — its branching is numerical physics, not decisions
of the kind ADR 0004/0018 carve out for engines. This directory is therefore
**third-party, not first-party**: the Zivo conventions (zero-if scanner,
Mantine-only UI, allowed-imports list) do not apply inside it. Everything Zivo
adds around the lab — the route, the mount, the practice-hub and door-page
entries — is first-party and follows the conventions.

## Local adaptations (re-apply after any source refresh)

1. **CSS scoping** — run `python3 scope-css.py` from this directory. It is
   idempotent; see its docstring for the rules. Summary:
   - element / attribute selectors get a `.bscope ` descendant prefix so the
     lab cannot restyle Zivo (`body` → `.bscope`, `button` → `.bscope button`);
   - `:root` custom properties stay global because the engine reads tokens off
     `document.documentElement`; the one real rooted declaration
     (`color-scheme: dark`) is split into a `.bscope[data-theme='dark']`
     mirror that `DesignLab` feeds by mirroring the attribute;
   - class selectors are left untouched (verified: zero class-name collisions
     against Zivo's stylesheets, which also keeps body-parked drag-image
     nodes like `.pal-carry` styled).
2. **Height** — `.app` uses `height: 100%` instead of `100vh`/`100dvh`: the
   lab fills the shell's content area rather than claiming the viewport.
3. **Naming** — user-facing strings, storage keys (`designlab.*.v1`), the
   design-file extension (`.designlab`) and the wordmark are Zivo-branded.
4. **Theming is Zivo's** — the engine's own Light/Dark/System setting and its
   boot-time applier were removed. `DesignLab` projects Zivo's color scheme
   (`data-mantine-color-scheme` on `<html>`, driven by the sidebar's
   dark/light toggle) onto the engine's `data-theme` attribute, so one switch
   skins the whole product. "light" is pinned explicitly so the engine's
   `prefers-color-scheme` fallback can never disagree with Zivo while the lab
   is open.

## Zivo-side wiring

- Route: `frontend/app/(shell)/practice/system-design/lab/page.tsx`
  (static segment, so it wins over the `[id]` problem route).
- Mount + theme mirror: `.../lab/_components/DesignLab.tsx` (first-party).
- Scanner: `frontend/vendor/` is exempt from `scripts/scan-no-if.py` via
  `SKIP_PATH_PARTS`; ESLint ignores `vendor/**`; the specs are kept out of the
  Next type-check via the frontend `tsconfig` `exclude` (the vendored sources
  still type-check — the mount imports them).
- Dependency: `lucide-react` pinned **exact `1.35.0`** — the icon set the
  engine's canvas draws from, used only inside this directory (Zivo's own UI
  keeps `@tabler/icons-react`). The pin matters: newer lucide releases renamed
  the deep-export `__iconNode` that `src/components/nodeVisuals.ts` draws
  from, which breaks the build. Revisit on refresh.
