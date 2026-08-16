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

from app.engine_runtime import pick as choose_branch

AUDIOBOOK_VERSION = "qb.audiobook.v1"

_CHUNK_TARGET_CHARS = 700
_CHUNK_MAX_CHARS = 1100

DEFAULT_VOICE = "en_US-lessac-medium"
VOICES = ("en_US-lessac-medium", "en_US-amy-medium", "en_GB-alan-medium")

DEFAULT_LENGTH_SCALE = 0.92

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
    paragraphs = list(
        filter(
            lambda p: len(p) >= _MIN_PARAGRAPH_CHARS,
            (_clean(p) for p in (text or "").split("\n\n")),
        )
    )

    def _merge() -> ChunkPlan:
        chunks: list[str] = []
        current = ""

        def _flush_long(para: str) -> None:
            nonlocal current
            choose_branch(bool(current), lambda: chunks.append(current), lambda: None)
            current = ""
            chunks.extend(_split_long_paragraph(para))

        def _merge_para(para: str) -> None:
            nonlocal current
            overflow = bool(current) and len(current) + len(para) + 1 > _CHUNK_TARGET_CHARS
            choose_branch(
                overflow,
                lambda: (chunks.append(current), None),
                lambda: None,
            )
            current = choose_branch(
                overflow,
                lambda: para,
                lambda: choose_branch(bool(current), lambda: f"{current} {para}".strip(), lambda: para),
            )

        for para in paragraphs:
            choose_branch(len(para) > _CHUNK_MAX_CHARS, lambda: _flush_long(para), lambda: _merge_para(para))
        choose_branch(bool(current), lambda: chunks.append(current), lambda: None)
        final: list[str] = []
        for c in chunks:
            choose_branch(
                len(c) <= _CHUNK_MAX_CHARS,
                lambda: final.append(c),
                lambda: final.extend(_split_long_paragraph(c)),
            )
        return ChunkPlan(chunks=final, voice=resolved_voice)

    return choose_branch(not paragraphs, lambda: ChunkPlan(chunks=[], voice=resolved_voice), _merge)


def _split_long_paragraph(para: str) -> list[str]:
    """Split a paragraph longer than the hard cap on sentence boundaries."""
    import re

    sentences = re.split(r"(?<=[.!?])\s+", para)
    out: list[str] = []
    current = ""
    for s in sentences:
        overflow = bool(current) and len(current) + len(s) + 1 > _CHUNK_MAX_CHARS
        choose_branch(overflow, lambda: out.append(current), lambda: None)
        current = choose_branch(
            overflow,
            lambda: s,
            lambda: choose_branch(bool(current), lambda: f"{current} {s}".strip(), lambda: s),
        )
    choose_branch(bool(current), lambda: out.append(current), lambda: None)
    return out


def chunk_progress(done: int, total: int) -> int:
    """0..100 render progress for the orchestrator."""
    return choose_branch(
        total <= 0,
        lambda: 100,
        lambda: max(0, min(100, int(100 * done / total))),
    )
