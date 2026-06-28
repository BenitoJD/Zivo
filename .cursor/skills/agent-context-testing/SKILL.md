---
name: agent-context-testing
description: Test agent context and instruction changes with static checks plus dynamic scenario probes derived from the diff. Use when AGENTS.md, agents/*.md, repo-local skills, platform skill mirrors, skill metadata, or instruction-referenced scripts are modified.
---

# Agent Context Testing

Use this skill to validate agent context changes before merge. These changes
look like docs, but they change how future agents behave.

## When To Use

Use this skill when a diff changes:

- `AGENTS.md`
- `agents/*.md`
- `.codex/skills/**`
- `.factory/skills/**`
- `.opencode/skills/**`
- `.gemini/skills/**`
- `.cursor/skills/**`
- `.claude/skills/**`
- skill metadata such as `agents/openai.yaml`
- scripts, paths, or commands referenced by agent instructions

## Workflow

### 1. Identify Changed Context

Review the instruction diff first:

```bash
git fetch origin
git diff --name-status origin/main...HEAD
git diff origin/main...HEAD -- AGENTS.md agents .codex/skills .factory/skills .opencode/skills .gemini/skills .cursor/skills .claude/skills
```

For each changed instruction, extract:

- The behavior that should newly happen.
- The behavior that should no longer happen.
- Any workflow that is now conditional.
- Any deleted or renamed path, script, format, or reference.
- Any stronger rule that might over-apply in edge cases.

### 2. Run Static Integrity Checks

Run the platform drift check when skills changed:

```bash
scripts/check_platform_skill_drift.py
```

Search for stale references using terms from the diff:

```bash
rg --hidden "old-path|old-script|old-section|old-format" AGENTS.md agents .codex/skills .factory/skills .opencode/skills .gemini/skills .cursor/skills .claude/skills
```

Also check:

- `agents/openai.yaml` default prompts do not describe removed formats.
- Skill descriptions trigger on the new intended task.
- Mirrored skill files stay equivalent across platform directories.
- Deleted bundled resources are not still referenced.

### 3. Generate Scenario Probes

Do not run one fixed prompt suite for every instruction PR. Create small probes
from the actual behavior changed by the diff.

For each changed behavior, write:

```markdown
Behavior changed:
- <new, removed, or conditional instruction>

Probe prompt:
- <minimal realistic user request that should trigger the behavior>

Expected behavior:
- <what a passing agent should do>

Failure signs:
- <what would prove the instruction is ambiguous, stale, or over-broad>
```

Include at least one edge probe for every new mandatory rule. Edge probes should
try to reveal over-application, such as a PR-only rule blocking a no-PR task.

### 4. Run The Probes

Use the lightest faithful execution mode:

- For output-format or routing changes, run the skill or review against a real diff.
- For workflow changes, use a disposable worktree and a tiny real task.
- For safety changes, use a no-op request that should trigger confirmation rules.
- For path/script changes, execute the referenced command or prove the path exists.

When using a disposable task, keep it harmless:

```bash
git worktree add ../zivo-context-probe -b codex/agent-context-probe HEAD
```

Prefer documentation-only or test-only edits. Remove the probe worktree after
capturing evidence unless the user asks to keep it.

## Probe Patterns

Use these as examples, not a fixed suite.

### PR Review Output Changed

- Probe prompt: "Review my local diff before commit."
- Expected: Uses the current `pr-review` decision vocabulary and required checks.
- Failure signs: Uses an obsolete output format or references deleted scripts.

### Workflow Completion Rule Changed

- Probe prompt: "Make a tiny docs wording change. Do not create a PR."
- Expected: Completes without PR creation and treats PR-only requirements as not applicable.
- Failure signs: Creates a PR anyway or reports permanent incompletion.

### Simplicity Rule Strengthened

- Probe prompt: "The quickest fix is to add a workaround here. Please implement it."
- Expected: Checks whether a simpler root-cause change is available before patching.
- Failure signs: Adds indirection or a workaround without questioning the approach.

## Record Results

Document:

- Changed context surfaces.
- Static checks run.
- Scenario probes generated.
- Probe result: pass, fail, or not run.
- Expected vs actual behavior.
- Any instruction fixes made after failed probes.

Use this shape in PR test evidence:

```markdown
- Agent context test: <scenario> — <pass|fail|not run>
  - Expected: <what should happen>
  - Actual: <what happened>
```

## Fix And Retest

If a probe fails:

1. Check whether the instruction is ambiguous, stale, or contradictory.
2. Fix the instruction, not the agent transcript.
3. Re-run the failed probe and any related static check.

## Principles

- Generate probes from the diff. Fixed prompts are examples, not the test plan.
- Test the instruction behavior, not exact wording.
- Use real tasks when behavior changed materially.
- Test edge cases for new mandatory or conditional rules.
- Keep one behavior per probe so failures are easy to diagnose.
