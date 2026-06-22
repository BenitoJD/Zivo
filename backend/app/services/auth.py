import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, Header, HTTPException, Response
from jose import JWTError, jwt
import bcrypt
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Account, User

from app.services.usage import DEMO_COOKIE

settings = get_settings()

USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{2,31}$")


def validate_username(username: str) -> None:
    if not USERNAME_RE.match(username):
        raise HTTPException(status_code=400, detail="Invalid username format")


def validate_password(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password too short")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise HTTPException(status_code=400, detail="Password must include a letter and a number")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_session_token(account_id: uuid.UUID, remember: bool = False) -> tuple[str, str]:
    days = settings.session_remember_days if remember else settings.session_days
    exp = datetime.now(timezone.utc) + timedelta(days=days)
    csrf = secrets.token_urlsafe(32)
    payload = {"sub": str(account_id), "exp": exp, "csrf": csrf}
    token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    return token, csrf


def set_session_cookie(response: Response, token: str, remember: bool) -> None:
    max_age = (settings.session_remember_days if remember else settings.session_days) * 86400
    response.set_cookie(
        key="zivo_session",
        value=token,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=max_age,
        path="/",
    )


def get_current_user(
    db: Session = Depends(get_db),
    zivo_session: str | None = Cookie(default=None),
) -> User:
    if not zivo_session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(zivo_session, settings.secret_key, algorithms=["HS256"])
        account_id = uuid.UUID(payload["sub"])
    except (JWTError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid session") from exc

    user = db.get(Account, account_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def get_optional_user(
    db: Session = Depends(get_db),
    zivo_session: str | None = Cookie(default=None),
) -> Account | None:
    if not zivo_session:
        return None
    try:
        payload = jwt.decode(zivo_session, settings.secret_key, algorithms=["HS256"])
        account_id = uuid.UUID(payload["sub"])
    except (JWTError, ValueError):
        return None
    return db.get(Account, account_id)


def csrf_from_session_token(zivo_session: str) -> str:
    try:
        payload = jwt.decode(zivo_session, settings.secret_key, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid session") from exc
    csrf = payload.get("csrf")
    if not isinstance(csrf, str) or not csrf:
        raise HTTPException(status_code=401, detail="Invalid session")
    return csrf


def require_csrf(
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    zivo_session: str | None = Cookie(default=None),
) -> None:
    if settings.environment == "development" and not x_csrf_token:
        return
    if not zivo_session or not x_csrf_token:
        raise HTTPException(status_code=403, detail="CSRF token required")
    try:
        payload = jwt.decode(zivo_session, settings.secret_key, algorithms=["HS256"])
        if payload.get("csrf") != x_csrf_token:
            raise HTTPException(status_code=403, detail="CSRF mismatch")
    except JWTError as exc:
        raise HTTPException(status_code=403, detail="Invalid CSRF") from exc


def require_csrf_or_guest(
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    zivo_session: str | None = Cookie(default=None),
    zivo_demo_id: str | None = Cookie(default=None, alias=DEMO_COOKIE),
) -> None:
    """Signed-in users need CSRF; anonymous guests use httponly cookie (set on first response)."""
    if zivo_session:
        require_csrf(x_csrf_token, zivo_session)
        return
    # Guest — no session; cookie may not exist until the handler sets it on this request.
    return


def authenticate_user(db: Session, username: str, password: str) -> Account | None:
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        return None
    return user
