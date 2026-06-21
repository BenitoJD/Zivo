"""CSRF policy for signed-in vs guest requests."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services.auth import require_csrf_or_guest


def test_guest_upload_allowed_without_demo_cookie() -> None:
    require_csrf_or_guest(x_csrf_token=None, citepage_session=None, citepage_demo_id=None)


def test_signed_in_user_requires_csrf_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")

    with pytest.raises(HTTPException) as exc:
        require_csrf_or_guest(x_csrf_token=None, citepage_session="fake-session", citepage_demo_id="guest-1")
    assert exc.value.status_code == 403
