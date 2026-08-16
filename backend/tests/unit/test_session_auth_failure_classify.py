"""Mirror of frontend isSessionAuthFailure: keep 401 classification honest.

The FE must not wipe CSRF on every 401 (admin gates, missing guest, bad password).
Only true session-rejection details clear client auth state.
"""

from __future__ import annotations

from app.engine_runtime import pick


def is_session_auth_failure(status: int, detail: str) -> bool:
    def _check() -> bool:
        d = detail.lower()
        return (
            "invalid session" in d
            or "session revoked" in d
            or "user not found" in d
            or d == "not authenticated"
        )

    return pick(status != 401, lambda: False, _check)


def test_session_rejection_details_clear_state() -> None:
    assert is_session_auth_failure(401, "Invalid session")
    assert is_session_auth_failure(401, "Session revoked")
    assert is_session_auth_failure(401, "User not found")
    assert is_session_auth_failure(401, "Not authenticated")


def test_unrelated_401s_do_not_clear_state() -> None:
    assert not is_session_auth_failure(401, "Invalid credentials")
    assert not is_session_auth_failure(401, "Authentication required")
    assert not is_session_auth_failure(401, "Guest session required")
    assert not is_session_auth_failure(401, "Google signup expired - try again")
    assert not is_session_auth_failure(403, "CSRF mismatch")
    assert not is_session_auth_failure(404, "Not found")
