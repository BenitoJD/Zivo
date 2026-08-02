# Audiobook Engine

Converts a study source into a listenable audio narration — "make it an
audiobook" — using only open-source, self-hosted tech. No API keys, no
per-minute cost.

## Policy (the engine)

`backend/app/services/audiobook.py` — pure facade (`qb.audiobook.v1`):

- **Chunking** (`plan_audiobook`): the document's page texts are split on
  paragraph boundaries and greedily merged toward a ~700-char target (≈3 min
  at 150 wpm), hard-capped at 1100 chars with sentence-boundary splitting for
  over-long paragraphs. A chunk failure only re-renders that one chunk.
- **Voice** (`pick_voice`): stable profile selection per document kind
  (default `en_US-lessac-medium`, a compact MIT-licensed Piper VITS model).
- **Progress** (`chunk_progress`): 0..100 render progress for the API.

## Render (the I/O)

`backend/app/services/audiobook_worker.py`:

- Loads the document's RAG chunk texts (`load_document_chunk_texts`).
- Runs **Piper** (MIT, on-device VITS TTS) per chunk: `piper --model
  <voice>.onnx --length_scale 0.92 --output_file -`, then **ffmpeg**
  (LGPL) converts WAV → MP3. Falls back to WAV when ffmpeg is absent.
- Stores `audiobooks/<document_id>/<i>.mp3` in MinIO plus a `manifest.json`
  (`state` / `progress` / `count` / `voice`), so the API serves presigned
  playback URLs without re-rendering.

## Live path

- `POST /api/audiobook/{id}/build` — enqueues the `audiobook.build` ETA io
  job (off the request path). Idempotent: a `ready` manifest short-circuits.
- `GET /api/audiobook/{id}/status` — polls state + per-chunk playback URLs.
- Frontend "Listen" affordance (workspace source row) surfaces the player.

## Kill switch

`audiobook_enabled` (default off) + `piper_bin` / `piper_voices_dir` config.
Endpoints return 404 until the worker image ships Piper + a voices dir —
production behaviour is unchanged until explicitly enabled, same pattern as
Offline Mode (ADR 0006).

## Why open source

| Piece | License | Role |
|-------|---------|------|
| Piper | MIT | VITS neural TTS, CPU-only, ~100 MB models |
| ffmpeg | LGPL | WAV → MP3 |
| MinIO | AGPL | object storage (already in the stack) |
| ETA io worker | — | background render off the request path |
