---
name: manage-agent-context
description: Manage repo agent instructions across `AGENTS.md`, `agents/`, platform skill directories, and any legacy instruction docs. Use when the user asks to add, update, remove, move, or deduplicate agent instructions or instruction files.
---

# Manage Agent Context

## What does "agent context" mean?

See the Glossary in `AGENTS.md`.

Use this skill for changes to the repo's agent-instruction surface.

Instruction surfaces in scope:

- `AGENTS.md`
- `agents/*.md`
- platform skill directories: `.codex/skills/**/SKILL.md`,
  `.factory/skills/**/SKILL.md`, `.opencode/skills/**/SKILL.md`,
  `.gemini/skills/**/SKILL.md`, `.cursor/skills/**/SKILL.md`, and
  `.claude/skills/**/SKILL.md`
- legacy instruction docs under `docs/`
- scripts or files that reference moved instruction paths

This repository treats instruction changes as high-impact. Do not edit
instruction files immediately.

## Required workflow

Before making any instruction edit:

1. Understand the user's intent first.
2. Check the current instruction state before deciding what to edit.
3. Identify the likely canonical file and any overlapping copies.
4. If the right change is obvious after checking the current state, make
   the edit.
5. If the right change is not obvious, present the current state, explain
   the ambiguity or routing choice, and ask the user before patching.

When the user has already been explicit and the correct edit is clear after
inspection, proceed without adding extra back-and-forth.

## Routing rules

- Treat `AGENTS.md` as a crisp table of contents for the instruction
  system.
- Keep `AGENTS.md` short and routing-focused; it should point to the right
  detailed files instead of carrying large amounts of context itself.
- Put only repo-wide process, safety, skill-routing, and default-behavior
  summaries in `AGENTS.md`.
- Put coding-style or implementation-preference rules in
  `agents/coding-guidelines.md`.
- Put workflow-specific guidance in the narrowest relevant repo-local skill.
- Put non-skill agent instruction docs in `agents/`.
- Treat `docs/` as non-agent documentation unless a legacy instruction doc
  is being migrated out of it.
- Never move `AGENTS.md` out of the repo root.
- If non-skill instruction files are reorganized, move them under `agents/`
  and update every path/reference that points at them.

Example:

- If the user asks to update a coding instruction, update
  `agents/coding-guidelines.md` first, then inspect `AGENTS.md`, relevant
  skills, and docs for overlapping copies that should also be updated.
- If a repo-local skill changes in one platform directory, mirror the same
  change across the other platform skill directories and run
  `scripts/check_platform_skill_drift.py`.

## Add / update / remove workflow

### Add

1. Search all instruction surfaces for the same or overlapping rule.
2. If the instruction already exists, do not add it again.
3. Tell the developer it is already present and point to the existing file.
4. If it is missing, add it only in the most appropriate canonical place.
5. Tighten or remove nearby duplicates when needed to avoid split sources of
   truth.

### Update

1. Find the canonical instruction and every overlapping copy.
2. Update the canonical file first.
3. Update any mirrored or derived copies that must stay in sync.
4. Prefer consolidation over drift; if two files conflict, resolve the
   conflict instead of leaving both versions.

### Remove

1. Confirm the instruction should be removed rather than rewritten.
2. Remove the canonical instruction and any stale duplicates.
3. Repair surrounding wording so the section still reads cleanly.

## Editing rules

- Keep instruction text concise, durable, and imperative.
- Prefer the smallest edit that preserves clarity.
- When touching `AGENTS.md`, prefer linking or routing to the canonical
  detail file over copying the full instruction body into `AGENTS.md`.
- Do not create extra documentation files unless the new file is itself the
  right canonical home for instructions.
- When adding a new skill-related instruction, check whether `AGENTS.md`
  should also mention that skill in routing or inventory sections.
- When moving files, also update references in scripts, docs, and skills.

## Validation

After editing:

1. Re-run targeted searches for the old and new wording.
2. Confirm moved-file paths are updated everywhere relevant.
3. Check for duplicate or contradictory instruction copies.
4. Summarize what changed, what was intentionally left alone, and any areas
   that still need human judgment.
