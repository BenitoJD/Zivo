"""Audiobook Engine — convert study text into listenable audio.

Design: docs/AUDIOBOOK_ENGINE.md
Version: qb.audiobook.v1

Pure policy (ADR 0004 seam): given document text, decide how to split it into
speakable chunks (paragraphs merged up to a duration budget) and which voice
profile to use. Orchestration (the ETA job) loads the text, calls the facade,
then renders each chunk with the local TTS engine and stores the audio in
MinIO. No I/O lives here — every decision is a pure function.

TTS engine: Piper (MIT, on-device, CPU-only). No API keys, no per-minute cost;
a small VITS model renders a paragraph in well under a second on commodity
hardware. MP3 is produced via ffmpeg from Piper's WAV.
"""

from __future__ import annotations

from dataclasses import dataclass, field

AUDIOBOOK_VERSION = "qb.audiobook.v1"

# Speakable chunk budget. ~150 wpm → 700 chars ≈ 3 minutes of audio; long
# enough to feel like a chapter section, short enough that a TTS failure only
# re-renders one chunk.
_CHUNK_TARGET_CHARS = 700
_CHUNK_MAX_CHARS = 1100

# Piper voice profile. Multiple profiles can be registered; the engine picks by
# document kind (default → study narration). Voice names are Piper model ids.
DEFAULT_VOICE = "en_US-lessac-medium"
VOICES = ("en_US-lessac-medium", "en_US-amy-medium", "en_GB-alan-medium")

# Target speech rate (Piper --length_scale; lower = faster). 0.92 ≈ natural
# study narration pace.
DEFAULT_LENGTH_SCALE = 0.92

# A paragraph shorter than this is dropped as boilerplate (headings, "Hi.",
# page furniture). Real sentences are longer than ~20 chars.
_MIN_PARAGRAPH_CHARS = 20


@dataclass(frozen=True)
class ChunkPlan:
    chunks: list[str] = field(default_factory=list)
    voice: str = DEFAULT_VOICE
    length_scale: float = DEFAULT_LENGTH_SCALE
    policy_version: str = AUDIOBOOK_VERSION


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def pick_voice(document_kind: str | None = None) -> str:
    """Stable voice selection. Default study narration; documents can opt into
    a different profile later without changing the policy seam."""
    return DEFAULT_VOICE


def plan_audiobook(text: str, *, voice: str | None = None) -> ChunkPlan:
    """Split document text into speakable chunks.

    Paragraphs are merged greedily up to ``_CHUNK_TARGET_CHARS`` (a paragraph
    longer than the hard cap is split on sentence boundaries). Blank and
    boilerplate-short lines are dropped. Returns the ordered chunk list plus
    the voice profile the render step should use.
    """
    resolved_voice = voice or pick_voice()
    paragraphs = [
        _clean(p)
        for p in (text or "").split("\n\n")
        if len(_clean(p)) >= _MIN_PARAGRAPH_CHARS
    ]
    if not paragraphs:
        return ChunkPlan(chunks=[], voice=resolved_voice)

    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(para) > _CHUNK_MAX_CHARS:
            # Flush what's pending, then hard-split the long paragraph.
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long_paragraph(para))
            continue
        if current and len(current) + len(para) + 1 > _CHUNK_TARGET_CHARS:
            chunks.append(current)
            current = para
        else:
            current = f"{current} {para}".strip() if current else para
    if current:
        chunks.append(current)

    # A chunk must never exceed the hard cap — safety net for odd inputs.
    final: list[str] = []
    for c in chunks:
        if len(c) <= _CHUNK_MAX_CHARS:
            final.append(c)
        else:
            final.extend(_split_long_paragraph(c))
    return ChunkPlan(chunks=final, voice=resolved_voice)


def _split_long_paragraph(para: str) -> list[str]:
    """Split a paragraph longer than the hard cap on sentence boundaries."""
    import re

    sentences = re.split(r"(?<=[.!?])\s+", para)
    out: list[str] = []
    current = ""
    for s in sentences:
        if current and len(current) + len(s) + 1 > _CHUNK_MAX_CHARS:
            out.append(current)
            current = s
        else:
            current = f"{current} {s}".strip() if current else s
    if current:
        out.append(current)
    return out


def chunk_progress(done: int, total: int) -> int:
    """0..100 render progress for the orchestrator."""
    if total <= 0:
        return 100
    return max(0, min(100, int(100 * done / total)))
