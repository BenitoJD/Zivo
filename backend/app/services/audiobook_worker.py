"""Audiobook orchestration — plan chunks, render with Piper, store in MinIO.

Engine policy lives in :mod:`app.services.audiobook` (pure facade). This module
owns the I/O: reading the document's chunk texts, invoking the local Piper TTS
binary (open-source, MIT, CPU-only — no API cost), and persisting the rendered
MP3s to MinIO. Rendered audio is stored per chunk under
``audiobooks/<document_id>/<index>.mp3`` with a manifest JSON at the root, so
the API can serve a playlist without re-rendering.

Piper is invoked as a subprocess: ``piper --model <voice>.onnx --output_file
<chunk>.wav --length_scale <scale>`` then ffmpeg converts WAV → MP3. Both are
open-source and shipped in the worker image.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.audiobook import ChunkPlan, chunk_progress, plan_audiobook
from app.services.chunks import load_document_chunk_texts
from app.services.storage import get_json, presigned_get_url, put_json

logger = logging.getLogger(__name__)

AUDIOBOOK_PREFIX = "audiobooks"
MANIFEST_KEY = "manifest.json"
CONTENT_TYPE_MP3 = "audio/mpeg"


def _storage_key(document_id: uuid.UUID, filename: str) -> str:
    return f"{AUDIOBOOK_PREFIX}/{document_id}/{filename}"


def _read_manifest(key: str) -> dict:
    """Read the manifest JSON, tolerating a missing object (first run)."""
    try:
        raw = get_json(key)
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def audiobook_status(db: Session, document_id: uuid.UUID) -> dict:
    """Read-only status for the API: state, progress, per-chunk URLs."""
    manifest = _read_manifest(_storage_key(document_id, MANIFEST_KEY))
    state = manifest.get("state")
    if state is None:
        return {"state": "none", "progress": 0, "chunks": []}
    chunks = [
        {
            "index": i,
            "url": presigned_get_url(_storage_key(document_id, f"{i}.mp3")),
        }
        for i in range(int(manifest.get("count") or 0))
    ]
    return {
        "state": state,
        "progress": int(manifest.get("progress") or 0),
        "voice": manifest.get("voice"),
        "chunks": chunks,
    }


def build_audiobook(db: Session, document_id: uuid.UUID) -> dict:
    """Render the document's text to MP3 chunks and persist a manifest.

    Returns the status dict. Safe to re-run: a completed manifest short-circuits.
    Called from the ETA io handler.
    """
    settings = get_settings()
    if not settings.audiobook_enabled:
        return {"state": "disabled", "progress": 0, "chunks": []}

    manifest_key = _storage_key(document_id, MANIFEST_KEY)
    existing = _read_manifest(manifest_key)
    if existing.get("state") == "ready":
        return audiobook_status(db, document_id)

    chunks_text = load_document_chunk_texts(db, document_id)
    text = "\n\n".join(chunks_text)
    plan: ChunkPlan = plan_audiobook(text)
    if not plan.chunks:
        manifest = {"state": "failed", "progress": 0, "count": 0, "error": "empty_text"}
        put_json(manifest_key, manifest)
        return {"state": "failed", "progress": 0, "chunks": [], "error": "empty_text"}

    total = len(plan.chunks)
    rendered = 0
    for i, chunk_text in enumerate(plan.chunks):
        try:
            mp3 = _render_chunk(chunk_text, plan)
            _put_mp3(_storage_key(document_id, f"{i}.mp3"), mp3)
            rendered += 1
            if i % 5 == 0 or i == total - 1:
                manifest = {
                    "state": "building",
                    "progress": chunk_progress(rendered, total),
                    "count": total,
                    "voice": plan.voice,
                }
                put_json(manifest_key, manifest)
        except Exception:
            logger.exception("audiobook chunk %d failed for %s", i, document_id)
            manifest = {
                "state": "failed",
                "progress": chunk_progress(rendered, total),
                "count": total,
                "voice": plan.voice,
                "error": f"chunk_{i}",
            }
            put_json(manifest_key, manifest)
            return {"state": "failed", "progress": chunk_progress(rendered, total), "chunks": []}

    manifest = {
        "state": "ready",
        "progress": 100,
        "count": total,
        "voice": plan.voice,
    }
    put_json(manifest_key, manifest)
    return audiobook_status(db, document_id)


def _render_chunk(text: str, plan: ChunkPlan) -> bytes:
    """Run Piper → WAV, then ffmpeg → MP3. Returns MP3 bytes."""
    settings = get_settings()
    model = str(Path(settings.piper_voices_dir) / f"{plan.voice}.onnx")
    if not Path(model).exists():
        raise FileNotFoundError(f"Piper voice model missing: {model}")

    # piper_bin may be a bare binary or a command with args (e.g. "python -m piper").
    piper_cmd = settings.piper_bin.split()
    wav = subprocess.run(
        [
            *piper_cmd,
            "--model",
            model,
            "--length_scale",
            str(plan.length_scale),
            "--output_file",
            "-",  # stdout
        ],
        input=text.encode("utf-8"),
        capture_output=True,
        check=True,
        timeout=120,
    ).stdout

    if not shutil.which("ffmpeg"):
        # Fall back to WAV when ffmpeg isn't present (still playable).
        return wav
    mp3 = subprocess.run(
        ["ffmpeg", "-y", "-i", "-", "-codec:a", "libmp3lame", "-qscale:a", "6", "-f", "mp3", "-"],
        input=wav,
        capture_output=True,
        check=True,
        timeout=120,
    ).stdout
    return mp3


def _put_mp3(key: str, data: bytes) -> None:
    from app.services.storage import _internal_client

    settings = get_settings()
    _internal_client().put_object(
        Bucket=settings.minio_bucket,
        Key=key,
        Body=data,
        ContentType=CONTENT_TYPE_MP3,
    )


def audiobook_manifest_payload(document_id: uuid.UUID) -> dict:
    """JSON payload for the ETA job result (no binary)."""
    return {"document_id": str(document_id), "kind": "audiobook", "manifest": MANIFEST_KEY}
