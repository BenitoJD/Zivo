# Workspace

UI is implemented with **Mantine** in `frontend/app/workspace/` — no custom components. See [AGENTS.md](../AGENTS.md#frontend-ui).

## Learn mode (mobile-first)

- **MCQ** hero fills the viewport
- Bottom tabs: MCQ · Source · Chat
- Source/Chat open as full-height sheets
- Mastery gate + **"I'm ready for the next page"** CTA

## Desktop (`lg+`)

- 50/50 horizontal split: MCQ | Source+Chat stacked

## Test mode

- MCQ fullscreen; chat and file API return 403

## Routes

- `/workspace` — shell
- `/workspace/[artifactId]` — deep link
