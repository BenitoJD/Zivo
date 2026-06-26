# Mantine-only UI on the Calm Paper token system

**Date:** 2026-06-27 · **Status:** accepted

All frontend UI is built from Mantine 9 components themed through a single
`createTheme` in `frontend/app/providers.tsx:132`. No custom component library, no
Tailwind, no shadcn, no hand-rolled CSS files. The visual language ("Calm Paper" —
warm paper surfaces, ink text, lavender accent, `shadow="paper"`, pill radii) is
encoded entirely as theme tokens and consumed via Mantine props / CSS variables. The
full token list is in [AGENTS.md → Frontend UI](../../AGENTS.md#frontend-ui).

**Why.** One component system + one theme means every screen is consistent by default
and a token change (color, radius, shadow) propagates everywhere without touching
pages. Hardcoded hex or a second styling system would let screens drift apart.

**Considered and rejected.** Tailwind / shadcn / bespoke CSS — rejected because they
reintroduce per-component styling decisions and let the paper aesthetic fragment;
Mantine's theme API already expresses every token we need.

**Consequence.** New UI imports only `@mantine/*`, `@tabler/icons-react`, `next/*`,
`react`. Inline `style={}` is reserved for genuinely dynamic/animated values. Enforced
today by review + `npm run build`; an eslint rule banning hardcoded hex is a candidate
in [ADR 0005](0005-enforcement-and-known-divergences.md).
