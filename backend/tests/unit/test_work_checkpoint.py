"""Work Checkpoint Engine — plan, mark_done, resume, persistence."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.work_checkpoint import (
    CHECKPOINT_VERSION,
    WorkCheckpoint,
    from_dict,
    load,
    mark_done,
    plan,
    resume_work,
    save,
    to_dict,
)


def test_plan_and_progress() -> None:
    ckpt = plan(["a", "b", "c"])
    assert ckpt.items == ("a", "b", "c")
    assert ckpt.progress == 0
    assert list(ckpt.remaining) == ["a", "b", "c"]


def test_mark_done_updates_progress() -> None:
    ckpt = plan(["a", "b", "c"])
    ckpt = mark_done(ckpt, "a", "c")
    assert sorted(ckpt.done) == ["a", "c"]
    assert ckpt.progress == 66
    assert list(ckpt.remaining) == ["b"]


def test_mark_done_ignores_unknown_keys() -> None:
    ckpt = mark_done(plan(["a"]), "a", "zzz")
    assert sorted(ckpt.done) == ["a"]


def test_empty_plan_progress_is_100() -> None:
    assert plan([]).progress == 100


def test_roundtrip_dict() -> None:
    ckpt = mark_done(plan(["a", "b"]), "a")
    raw = to_dict(ckpt)
    assert raw["version"] == CHECKPOINT_VERSION
    restored = from_dict(raw)
    assert restored.items == ckpt.items
    assert restored.done == ckpt.done
    assert restored.version == CHECKPOINT_VERSION


def test_from_dict_garbage() -> None:
    ckpt = from_dict(None)
    assert ckpt.items == ()
    assert ckpt.done == frozenset()
    ckpt2 = from_dict({"items": "not-a-list"})
    assert ckpt2.items == ()


def test_save_and_load_roundtrip_job() -> None:
    db = MagicMock()
    job = MagicMock()
    job.result = None
    db.get.return_value = job
    ckpt = mark_done(plan(["a", "b"]), "a")
    save(db, ckpt, job_id="00000000-0000-4000-8000-000000000001")
    # result was set + committed
    assert job.result["work_checkpoint"]["done"] == ["a"]
    db.commit.assert_called()

    loaded = load(db, job_id="00000000-0000-4000-8000-000000000001")
    assert loaded.done == frozenset({"a"})


def test_load_missing_job_returns_empty() -> None:
    db = MagicMock()
    db.get.return_value = None
    assert load(db, job_id="00000000-0000-4000-8000-000000000001") == WorkCheckpoint()


_JID = "00000000-0000-4000-8000-000000000001"


def test_resume_work_plan_when_no_checkpoint() -> None:
    db = MagicMock()
    db.get.return_value = None
    ckpt = resume_work(db, items=["x", "y"], job_id=_JID)
    assert ckpt.items == ("x", "y")
    assert not ckpt.done


def test_resume_work_reuses_done_when_valid() -> None:
    db = MagicMock()
    job = MagicMock()
    job.result = {"work_checkpoint": {"items": ["a", "b"], "done": ["a"], "version": CHECKPOINT_VERSION}}
    db.get.return_value = job
    ckpt = resume_work(db, items=["a", "b"], job_id=_JID)
    assert sorted(ckpt.done) == ["a"]
    assert list(ckpt.remaining) == ["b"]


def test_resume_work_drops_stale_done_keys() -> None:
    """Plan changed (source edited) — done keys not in the new plan are dropped."""
    db = MagicMock()
    job = MagicMock()
    job.result = {"work_checkpoint": {"items": ["a", "b", "old"], "done": ["a", "old"], "version": CHECKPOINT_VERSION}}
    db.get.return_value = job
    ckpt = resume_work(db, items=["a", "b"], job_id=_JID)
    assert sorted(ckpt.done) == ["a"]  # "old" no longer valid


def test_resume_work_verifies_done_with_is_done() -> None:
    db = MagicMock()
    job = MagicMock()
    job.result = {"work_checkpoint": {"items": ["a", "b"], "done": ["a"], "version": CHECKPOINT_VERSION}}
    db.get.return_value = job
    # "a" artifact vanished (torn write) → dropped back into remaining.
    ckpt = resume_work(db, items=["a", "b"], job_id=_JID, is_done=lambda k: k == "b")
    assert not ckpt.done
    assert list(ckpt.remaining) == ["a", "b"]


def test_current_job_id_context() -> None:
    with patch("app.services.work_checkpoint.eta_context.get_current_job_id", return_value="11111111-1111-4111-8111-111111111111"):
        from app.services.work_checkpoint import current_job_id

        jid = current_job_id()
        assert jid is not None
        assert str(jid) == "11111111-1111-4111-8111-111111111111"
