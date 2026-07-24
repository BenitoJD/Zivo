"""Newspaper brand allowlist helpers."""

from app.repositories import newspaper as newspaper_repo


def test_is_brand_allowed_when_allowlist_off(monkeypatch) -> None:
    monkeypatch.setattr(
        newspaper_repo,
        "get_settings",
        lambda db: {"allowlist_only": False},
    )
    assert newspaper_repo.is_brand_allowed(object(), "mint") is True


def test_is_brand_allowed_requires_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        newspaper_repo,
        "get_settings",
        lambda db: {"allowlist_only": True},
    )

    class FakeResult:
        def first(self):
            return (True,)

    class FakeDB:
        def execute(self, *a, **k):
            return FakeResult()

    assert newspaper_repo.is_brand_allowed(FakeDB(), "mint") is True

    class FakeOff:
        def first(self):
            return (False,)

    class FakeDBOff:
        def execute(self, *a, **k):
            return FakeOff()

    assert newspaper_repo.is_brand_allowed(FakeDBOff(), "et") is False
