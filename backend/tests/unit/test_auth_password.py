"""Password hashing for signup/login."""

from app.services.auth import hash_password, verify_password


def test_hash_and_verify_password() -> None:
    hashed = hash_password("password123")
    assert hashed != "password123"
    assert verify_password("password123", hashed)
    assert not verify_password("wrong-password", hashed)
