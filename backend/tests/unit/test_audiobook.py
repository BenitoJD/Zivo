"""Audiobook Engine — chunking policy + worker orchestration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.audiobook import (
    AUDIOBOOK_VERSION,
    _CHUNK_MAX_CHARS,
    DEFAULT_VOICE,
    chunk_progress,
    pick_voice,
    plan_audiobook,
)


# --- plan_audiobook ---------------------------------------------------------


def test_plan_empty_text() -> None:
    plan = plan_audiobook("")
    assert plan.chunks == []
    assert plan.voice == DEFAULT_VOICE
    assert plan.policy_version == AUDIOBOOK_VERSION


def test_plan_short_paragraphs_merged() -> None:
    text = "First paragraph about plants.\n\nSecond paragraph about light."
    plan = plan_audiobook(text)
    assert len(plan.chunks) == 1
    assert "First paragraph" in plan.chunks[0]
    assert "Second paragraph" in plan.chunks[0]


def test_plan_many_paragraphs_split_to_target() -> None:
    para = "This is a paragraph with enough words to be a speakable unit. " * 12
    text = "\n\n".join([para] * 20)
    plan = plan_audiobook(text)
    assert len(plan.chunks) > 1
    assert all(len(c) <= _CHUNK_MAX_CHARS for c in plan.chunks)


def test_plan_long_paragraph_split_on_sentences() -> None:
    sentence = "The chloroplast is the site of photosynthesis in green plants. " * 40
    plan = plan_audiobook(sentence)
    assert len(plan.chunks) >= 2
    assert all(len(c) <= _CHUNK_MAX_CHARS for c in plan.chunks)


def test_plan_skips_boilerplate_short_lines() -> None:
    plan = plan_audiobook("Hi.\n\n\n\n\n  \n\nReal content paragraph here.")
    assert all(len(c) >= 20 for c in plan.chunks)


def test_plan_voice_override() -> None:
    plan = plan_audiobook("Some text here.", voice="en_GB-alan-medium")
    assert plan.voice == "en_GB-alan-medium"


# --- pick_voice -------------------------------------------------------------


def test_pick_voice_default() -> None:
    assert pick_voice() == DEFAULT_VOICE
    assert pick_voice("pdf") == DEFAULT_VOICE


# --- chunk_progress ---------------------------------------------------------


def test_chunk_progress() -> None:
    assert chunk_progress(0, 10) == 0
    assert chunk_progress(5, 10) == 50
    assert chunk_progress(10, 10) == 100
    assert chunk_progress(3, 0) == 100
    assert chunk_progress(7, 3) == 100  # clamped


# --- audiobook_worker -------------------------------------------------------


def test_build_audiobook_disabled() -> None:
    from app.services.audiobook_worker import build_audiobook

    db = MagicMock()
    with patch("app.services.audiobook_worker.get_settings") as gs:
        gs.return_value.audiobook_enabled = False
        out = build_audiobook(db, "00000000-0000-4000-8000-000000000001")
    assert out["state"] == "disabled"


def test_build_audiobook_ready_short_circuits() -> None:
    from app.services.audiobook_worker import build_audiobook

    db = MagicMock()
    with (
        patch("app.services.audiobook_worker.get_settings") as gs,
        patch("app.services.audiobook_worker.get_json", return_value={"state": "ready", "count": 3}),
        patch("app.services.audiobook_worker.audiobook_status", return_value={"state": "ready"}) as st,
    ):
        gs.return_value.audiobook_enabled = True
        out = build_audiobook(db, "00000000-0000-4000-8000-000000000001")
    assert out == {"state": "ready"}
    st.assert_called_once()


def test_build_audiobook_empty_text_fails() -> None:
    from app.services.audiobook_worker import build_audiobook

    db = MagicMock()
    with (
        patch("app.services.audiobook_worker.get_settings") as gs,
        patch("app.services.audiobook_worker.get_json", return_value={}),
        patch("app.services.audiobook_worker.load_document_chunk_texts", return_value=[]),
        patch("app.services.audiobook_worker.put_json") as pj,
    ):
        gs.return_value.audiobook_enabled = True
        out = build_audiobook(db, "00000000-0000-4000-8000-000000000001")
    assert out["state"] == "failed"
    pj.assert_called_once()


def test_build_audiobook_renders_and_persists() -> None:
    from app.services.audiobook_worker import build_audiobook

    db = MagicMock()
    # Each paragraph is long enough that plan_audiobook keeps them as two chunks.
    chunks = ["Alpha content paragraph " + "with many words " * 60, "Beta content paragraph " + "with many words " * 60]
    with (
        patch("app.services.audiobook_worker.get_settings") as gs,
        patch("app.services.audiobook_worker.get_json", return_value={}),
        patch("app.services.audiobook_worker.load_document_chunk_texts", return_value=chunks),
        patch("app.services.audiobook_worker._render_chunk", return_value=b"MP3DATA") as rc,
        patch("app.services.audiobook_worker._put_mp3") as pm,
        patch("app.services.audiobook_worker.put_json") as pj,
        patch("app.services.audiobook_worker.audiobook_status", return_value={"state": "ready", "count": 2}) as st,
    ):
        gs.return_value.audiobook_enabled = True
        out = build_audiobook(db, "00000000-0000-4000-8000-000000000001")
    assert rc.call_count == 2
    assert pm.call_count == 2
    assert out == {"state": "ready", "count": 2}
    pj.assert_called()  # manifest writes
    st.assert_called_once()


def test_build_audiobook_chunk_failure_marks_failed() -> None:
    from app.services.audiobook_worker import build_audiobook

    db = MagicMock()
    with (
        patch("app.services.audiobook_worker.get_settings") as gs,
        patch("app.services.audiobook_worker.get_json", return_value={}),
        patch("app.services.audiobook_worker.load_document_chunk_texts", return_value=["Only chunk."]),
        patch(
            "app.services.audiobook_worker._render_chunk",
            side_effect=RuntimeError("piper missing"),
        ),
        patch("app.services.audiobook_worker.put_json") as pj,
    ):
        gs.return_value.audiobook_enabled = True
        out = build_audiobook(db, "00000000-0000-4000-8000-000000000001")
    assert out["state"] == "failed"
    pj.assert_called()


def test_audiobook_status_none() -> None:
    from app.services.audiobook_worker import audiobook_status

    db = MagicMock()
    with patch("app.services.audiobook_worker.get_json", return_value=None):
        out = audiobook_status(db, "00000000-0000-4000-8000-000000000001")
    assert out["state"] == "none"


def test_audiobook_status_ready_with_urls() -> None:
    from app.services.audiobook_worker import audiobook_status

    db = MagicMock()
    manifest = {"state": "ready", "progress": 100, "count": 2, "voice": DEFAULT_VOICE}
    with (
        patch("app.services.audiobook_worker.get_json", return_value=manifest),
        patch("app.services.audiobook_worker.presigned_get_url", return_value="https://minio/audio.mp3") as pgu,
    ):
        out = audiobook_status(db, "00000000-0000-4000-8000-000000000001")
    assert out["state"] == "ready"
    assert out["progress"] == 100
    assert out["voice"] == DEFAULT_VOICE
    assert len(out["chunks"]) == 2
    assert pgu.call_count == 2


def test_build_audiobook_resumes_from_done_chunks() -> None:
    """A prior crash left done=[0]; a retry must skip chunk 0 and render 1."""
    from app.services.audiobook_worker import build_audiobook

    db = MagicMock()
    chunks = ["Alpha content paragraph " + "with many words " * 60, "Beta content paragraph " + "with many words " * 60]
    with (
        patch("app.services.audiobook_worker.get_settings") as gs,
        patch(
            "app.services.audiobook_worker.get_json",
            return_value={"state": "building", "done": [0], "count": 2, "voice": DEFAULT_VOICE},
        ),
        patch("app.services.audiobook_worker.load_document_chunk_texts", return_value=chunks),
        patch("app.services.audiobook_worker._object_exists", return_value=True),
        patch("app.services.audiobook_worker._render_chunk", return_value=b"MP3DATA") as rc,
        patch("app.services.audiobook_worker._put_mp3") as pm,
        patch("app.services.audiobook_worker.put_json") as pj,
        patch("app.services.audiobook_worker.audiobook_status", return_value={"state": "ready", "count": 2}) as st,
    ):
        gs.return_value.audiobook_enabled = True
        out = build_audiobook(db, "00000000-0000-4000-8000-000000000001")
    # Only chunk 1 is re-rendered; chunk 0 skipped (already done + exists).
    assert rc.call_count == 1
    assert pm.call_count == 1
    assert out == {"state": "ready", "count": 2}
    st.assert_called_once()
    # Final manifest records both chunks done.
    final_manifest = pj.call_args_list[-1][0][1]
    assert final_manifest["done"] == [0, 1]


def test_build_audiobook_narration_step_used() -> None:
    """build_audiobook adapts text via the narration graph before chunking."""
    from app.services.audiobook_worker import build_audiobook

    db = MagicMock()
    with (
        patch("app.services.audiobook_worker.get_settings") as gs,
        patch("app.services.audiobook_worker.get_json", return_value={}),
        patch("app.services.audiobook_worker.load_document_chunk_texts", return_value=["Raw bullet text."]),
        patch(
            "app.graphs.audiobook_graph.adapt_document_for_narration",
            return_value=["Narrated flowing prose about the bullet."],
        ) as adapt,
        patch("app.services.audiobook_worker._render_chunk", return_value=b"MP3DATA"),
        patch("app.services.audiobook_worker._put_mp3"),
        patch("app.services.audiobook_worker.put_json"),
        patch("app.services.audiobook_worker.audiobook_status", return_value={"state": "ready", "count": 1}),
    ):
        gs.return_value.audiobook_enabled = True
        build_audiobook(db, "00000000-0000-4000-8000-000000000001")
    adapt.assert_called_once()


def test_render_chunk_missing_model_raises() -> None:
    from app.services.audiobook_worker import _render_chunk
    from app.services.audiobook import ChunkPlan

    plan = ChunkPlan(chunks=["x"], voice=DEFAULT_VOICE)
    with patch("app.services.audiobook_worker.get_settings") as gs:
        gs.return_value.piper_voices_dir = "/nonexistent/voices"
        with pytest.raises(FileNotFoundError):
            _render_chunk("hello", plan)
