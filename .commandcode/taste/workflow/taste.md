# Workflow Preferences

- Prefers deploying via GitHub Actions workflow (`gh workflow run "Deploy Zivo" --ref main`) rather than manual deploy commands. Confidence: 0.95
- Git flow: branch from main → commit → fetch + ff-merge onto origin/main → push → deploy → delete branch. Always resets `frontend/next-env.d.ts` after builds. Confidence: 0.95
- Dev via `./scripts/dev.sh start` (API on :8200/:8201, CPU/IO workers, frontend on :3000, logs in `logs/zivo-dev/`). Confidence: 0.9
- Commits frequently during a session and deploys after every meaningful batch of work — does not batch up many changes for a single deploy. Confidence: 0.85
- Gives full production access and permission ("do whatever you want, we're in stealth mode, no users"). Downtime and data loss are acceptable at this stage. Confidence: 0.95
- Background tasks (deploy watchers, long builds) should run as detached background commands and report through task notifications. Confidence: 0.8
- Testing stack: backend `pytest tests/unit` (ruff E9,F gate), frontend `npx tsc --noEmit` + `npm run build`, headless Playwright for UI flows from Bash. Confidence: 0.9
- Prefers `git fetch origin && git merge --ff-only origin/main` over rebase when possible, and uses cherry-pick recovery when rebase tangles. Confidence: 0.8
- When merging test trees, run the FULL test suite (both `tests/` and any `tests_citepage/` trees) before declaring green. Confidence: 0.85
- Wants documentation written for hard-fought fixes so future debugging is faster — e.g., the Judge0 isolate stack-limit fix. Confidence: 0.8
- Prefers running production verification (Playwright against prod URL) after every deploy to confirm the changes landed. Confidence: 0.85
- Uses memory files for durable project knowledge, but the agent should keep them current and delete stale ones. Confidence: 0.8
