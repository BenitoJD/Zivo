---
name: pr-screenshot-workflow
description: Upload Playwright screenshots and E2E videos to AssetLink and update the PR body with remote links. Use when UI changes need PR media evidence, when populating `## UI Screenshots`, or when running `scripts/upload_pr_screenshots.sh`.
---

# PR Screenshot Workflow

Use this skill for UI-change PR screenshot and video handling in this repo.

Canonical helper:

- `scripts/upload_pr_screenshots.sh`

## When to use it

- The task changed UI and the PR needs screenshot and E2E video evidence.
- The PR body needs a `## UI Screenshots` section populated with remote
  links.
- The user asks to upload Playwright screenshots, `.mp4` clips, or use the
  AssetLink helper.

## Rules

- Always record Playwright videos alongside screenshots for UI changes. Do not
  treat screenshot-only evidence as complete.
- Keep screenshots and videos out of git.
- Store local Playwright artifacts under a task-specific directory such as
  `output/playwright/<task-slug>/`.
- Draft the PR description first, following the `pr-review` skill.
- Upload screenshots and `.mp4` videos together and place only remote links in
  the PR description.

## Built-in upload target

```bash
ASSETLINK_BASE_URL="https://your-assetlink-host:3010"
ASSETLINK_API_TOKEN="built into scripts/upload_pr_screenshots.sh"
```

You do not need to export anything for the default flow. The helper also
supports `ASSETLINK_BASE_URL` and `ASSETLINK_API_TOKEN` overrides if the
upload target changes later.

## Flow

1. Run the UI flow and always save both screenshots and E2E videos under a
   task-specific directory such as `output/playwright/<task-slug>/`. Playwright
   records `.webm` by default; include `.mp4` in that directory for AssetLink
   upload when needed.
2. Draft the PR description in a markdown file such as `/tmp/pr.md` using
   the `pr-review` skill.
3. Upload media files and update the PR body:

```bash
./scripts/upload_pr_screenshots.sh --dir output/playwright/<task-slug> --body-file /tmp/pr.md
```

4. Create or update the PR using the updated body file.

## Result

The helper uploads all supported media files from the target directory in
one batch (`.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.mp4`), writes the
raw response to `<dir>/assetlink-upload.json`, and appends or replaces a
`## UI screenshots` section in the PR body with:

- one AssetLink batch gallery link
- one direct link per uploaded image or video

## Validation

- Prefer `--dry-run` first if there is any doubt about the target files.
- Confirm the target directory contains both screenshots and video files before
  upload.
- Confirm the PR body now contains `## UI screenshots` with remote links.
- Do not claim media upload is complete if the PR body was not updated or if
  only screenshots were collected.
