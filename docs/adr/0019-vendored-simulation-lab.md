# The System Design lab ships as a vendored engine

**Date:** 2026-09-10 · **Status:** accepted · **Exempts:** vendored code only, from [ADR 0018](0018-zero-if-engine-tables.md) and the Mantine-only UI rules for files inside `frontend/vendor/`

The System Design simulation lab (`/practice/system-design/lab`) ships as
vendored source under `frontend/vendor/system-design-lab/` (scope, adaptations,
and maintenance procedure: [frontend/vendor/system-design-lab/VENDOR.md](../../frontend/vendor/system-design-lab/VENDOR.md)).

**Why vendor instead of port.** The lab's entire value is simulation
fidelity — "the numbers have to be true." The discrete-event engine and its
hand-rolled SVG canvas are load-bearing: porting them to Mantine and zero-if
rule tables would fork the physics, and every future fix would become a manual
re-derivation. A simulator is an instrument, not a policy: its branching is
numerical physics, not decisions of the kind ADR 0004/0018 carve out for
engines.

**Scope of the exemption.** The no-if scanner (`SKIP_PATH_PARTS`), ESLint,
and the Mantine-only/Calm-Paper UI rules do not apply inside
`frontend/vendor/`. They apply fully to everything Zivo adds around it: the
route, the mount (`DesignLab.tsx`), the practice-hub and door-page entries,
and their icons (`@tabler/icons-react`, not the vendored engine's own icon
dependency, which is used inside the vendor directory alone). The vendored
TypeScript is still type-checked by `next build` (the mount imports it); the
vendored Vitest specs ride along unrun — they are exercised against the
source checkout when fidelity questions come up.

**Consequences.** Source refreshes re-copy `src/`, re-run the idempotent
`scope-css.py`, and re-apply the one-line `.app` height adaptation — all
documented in VENDOR.md. Zivo class names must never collide with the lab's
CSS classes (checked at integration time; zero collisions). The MIT license
file in the vendor directory stays intact.
