from urllib.parse import quote

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Account as User
from app.services.auth import (
    authenticate_user,
    clear_session_cookie,
    cookie_delete_kwargs,
    cookie_set_kwargs,
    create_session_token,
    csrf_from_session_token,
    get_current_user,
    get_optional_user,
    hash_password,
    require_csrf,
    set_session_cookie,
    validate_password,
    validate_username,
)
from app.services.google_oauth import (
    PENDING_COOKIE,
    PENDING_COOKIE_MAX_AGE,
    STATE_COOKIE,
    build_google_authorize_url,
    exchange_code_for_userinfo,
    frontend_path,
    google_oauth_configured,
    mint_pending_token,
    new_oauth_state,
    read_pending_token,
    require_google_oauth,
)
from app.services.guest import claim_guest_documents, claim_guest_progress
from app.services.guest_session import publish_guest_id, read_guest_id_from_cookie
from app.services.rate_limit import rate_limit_dependency
from app.services.usage import DEMO_COOKIE, ensure_demo_cookie

router = APIRouter()


class SignUpRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    confirm_password: str
    accept_terms: bool


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    remember_me: bool = False


class GoogleCompleteRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    accept_terms: bool


class AuthResponse(BaseModel):
    username: str
    csrf_token: str
    is_admin: bool = False


class SessionResponse(BaseModel):
    """Session probe result. Anonymous/guest visitors get a 200 with
    `authenticated: false` (not a 401), so the browser console stays clean."""

    authenticated: bool = False
    username: str | None = None
    csrf_token: str | None = None
    is_admin: bool = False


class GoogleStatusResponse(BaseModel):
    enabled: bool


def _oauth_error_redirect(message: str) -> RedirectResponse:
    settings = get_settings()
    url = frontend_path(settings, f"/login?error={quote(message)}")
    return RedirectResponse(url=url, status_code=302)


def _clear_oauth_cookies(response: Response) -> None:
    kwargs = cookie_delete_kwargs()
    response.delete_cookie(STATE_COOKIE, **kwargs)
    response.delete_cookie(PENDING_COOKIE, **kwargs)


@router.post("/guest")
def mint_guest_session(
    response: Response,
    zivo_demo_id: str | None = Cookie(default=None, alias=DEMO_COOKIE),
) -> dict[str, str]:
    """Mint or refresh the anonymous guest cookie without listing sources."""
    guest_id = ensure_demo_cookie(response, read_guest_id_from_cookie(zivo_demo_id))
    publish_guest_id(response, guest_id)
    return {"guest_id": guest_id}


@router.post("/signup", response_model=AuthResponse, dependencies=[Depends(rate_limit_dependency)])
def signup(
    body: SignUpRequest,
    response: Response,
    db: Session = Depends(get_db),
    zivo_demo_id: str | None = Cookie(default=None, alias=DEMO_COOKIE),
) -> AuthResponse:
    if not body.accept_terms:
        raise HTTPException(status_code=400, detail="Terms must be accepted")
    if body.password != body.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    validate_username(body.username)
    validate_password(body.password)

    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(status_code=409, detail="Username taken")

    user = User(username=body.username, password_hash=hash_password(body.password))
    try:
        db.add(user)
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Username taken") from None

    guest_id = read_guest_id_from_cookie(zivo_demo_id)
    claim_guest_documents(db, user.id, guest_id)
    claim_guest_progress(db, user.id, user.username, guest_id)

    token, csrf = create_session_token(
        user.id, int(getattr(user, "session_version", 0) or 0), remember=True
    )
    set_session_cookie(response, token, remember=True)
    return AuthResponse(username=user.username, csrf_token=csrf, is_admin=user.is_admin)


@router.post("/login", response_model=AuthResponse, dependencies=[Depends(rate_limit_dependency)])
def login(
    body: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
    zivo_demo_id: str | None = Cookie(default=None, alias=DEMO_COOKIE),
) -> AuthResponse:
    user = authenticate_user(db, body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    guest_id = read_guest_id_from_cookie(zivo_demo_id)
    claim_guest_documents(db, user.id, guest_id)
    claim_guest_progress(db, user.id, user.username, guest_id)

    token, csrf = create_session_token(
        user.id, int(getattr(user, "session_version", 0) or 0), remember=body.remember_me
    )
    set_session_cookie(response, token, remember=body.remember_me)
    return AuthResponse(username=user.username, csrf_token=csrf, is_admin=user.is_admin)


@router.get("/google/status", response_model=GoogleStatusResponse)
def google_status() -> GoogleStatusResponse:
    return GoogleStatusResponse(enabled=google_oauth_configured(get_settings()))


@router.get("/google")
def google_start(response: Response) -> RedirectResponse:
    settings = get_settings()
    require_google_oauth(settings)
    state = new_oauth_state()
    response = RedirectResponse(url=build_google_authorize_url(settings, state), status_code=302)
    response.set_cookie(
        key=STATE_COOKIE,
        value=state,
        **cookie_set_kwargs(max_age=600),
    )
    return response


@router.get("/google/callback")
def google_callback(
    response: Response,
    db: Session = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    zivo_google_oauth_state: str | None = Cookie(default=None, alias=STATE_COOKIE),
    zivo_demo_id: str | None = Cookie(default=None, alias=DEMO_COOKIE),
) -> RedirectResponse:
    settings = get_settings()
    if error:
        return _oauth_error_redirect("Google sign-in was cancelled")
    if not code or not state or not zivo_google_oauth_state or state != zivo_google_oauth_state:
        return _oauth_error_redirect("Google sign-in failed (invalid state)")

    try:
        info = exchange_code_for_userinfo(settings, code)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "Google sign-in failed"
        return _oauth_error_redirect(detail)

    google_sub = info["google_sub"]
    email = info["email"]

    existing = db.query(User).filter(User.google_sub == google_sub).first()
    if existing:
        guest_id = read_guest_id_from_cookie(zivo_demo_id)
        claim_guest_documents(db, existing.id, guest_id)
        claim_guest_progress(db, existing.id, existing.username, guest_id)
        token, _csrf = create_session_token(
            existing.id,
            int(getattr(existing, "session_version", 0) or 0),
            remember=True,
        )
        redirect = RedirectResponse(url=frontend_path(settings, "/workspace"), status_code=302)
        set_session_cookie(redirect, token, remember=True)
        _clear_oauth_cookies(redirect)
        return redirect

    # Email already used by a password account — do not auto-link (takeover risk).
    email_owner = db.query(User).filter(User.email == email).first()
    if email_owner and not email_owner.google_sub:
        return _oauth_error_redirect("Email already registered — sign in with password")

    pending = mint_pending_token(settings, google_sub=google_sub, email=email)
    redirect = RedirectResponse(url=frontend_path(settings, "/signup/google"), status_code=302)
    redirect.set_cookie(
        key=PENDING_COOKIE,
        value=pending,
        **cookie_set_kwargs(max_age=PENDING_COOKIE_MAX_AGE),
    )
    redirect.delete_cookie(STATE_COOKIE, **cookie_delete_kwargs())
    return redirect


@router.get("/google/pending")
def google_pending(
    zivo_google_pending: str | None = Cookie(default=None, alias=PENDING_COOKIE),
) -> dict[str, str | bool]:
    """Probe whether a Google signup is waiting for a username."""
    settings = get_settings()
    if not zivo_google_pending:
        return {"pending": False}
    try:
        _sub, email = read_pending_token(settings, zivo_google_pending)
    except HTTPException:
        return {"pending": False}
    return {"pending": True, "email": email}


@router.post(
    "/google/complete",
    response_model=AuthResponse,
    dependencies=[Depends(rate_limit_dependency)],
)
def google_complete(
    body: GoogleCompleteRequest,
    response: Response,
    db: Session = Depends(get_db),
    zivo_google_pending: str | None = Cookie(default=None, alias=PENDING_COOKIE),
    zivo_demo_id: str | None = Cookie(default=None, alias=DEMO_COOKIE),
) -> AuthResponse:
    settings = get_settings()
    if not zivo_google_pending:
        raise HTTPException(status_code=401, detail="Google signup expired — try again")
    if not body.accept_terms:
        raise HTTPException(status_code=400, detail="Terms must be accepted")
    validate_username(body.username)

    google_sub, email = read_pending_token(settings, zivo_google_pending)

    if db.query(User).filter(User.google_sub == google_sub).first():
        raise HTTPException(status_code=409, detail="Google account already linked")
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(status_code=409, detail="Username taken")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="Email already registered — sign in with password")

    user = User(
        username=body.username,
        password_hash=None,
        email=email,
        google_sub=google_sub,
    )
    try:
        db.add(user)
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Username or email taken") from None

    guest_id = read_guest_id_from_cookie(zivo_demo_id)
    claim_guest_documents(db, user.id, guest_id)
    claim_guest_progress(db, user.id, user.username, guest_id)

    token, csrf = create_session_token(
        user.id, int(getattr(user, "session_version", 0) or 0), remember=True
    )
    set_session_cookie(response, token, remember=True)
    _clear_oauth_cookies(response)
    return AuthResponse(username=user.username, csrf_token=csrf, is_admin=user.is_admin)


@router.get("/session", response_model=SessionResponse)
def get_session(
    user: User | None = Depends(get_optional_user),
    zivo_session: str | None = Cookie(default=None),
) -> SessionResponse:
    # Not being signed in is the normal case (guests), not an error — return an
    # empty 200 session instead of a 401 so the browser doesn't log a failed request.
    if not zivo_session or user is None:
        return SessionResponse(authenticated=False)
    return SessionResponse(
        authenticated=True,
        username=user.username,
        csrf_token=csrf_from_session_token(zivo_session),
        is_admin=user.is_admin,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(require_csrf),
) -> Response:
    # Injected Response starts with status_code=None; returning it without setting
    # 204 yields an incomplete ASGI response (empty reply from uvicorn). Set the
    # status explicitly, then return the same instance so Set-Cookie is kept.
    user.session_version = int(getattr(user, "session_version", 0) or 0) + 1
    db.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    clear_session_cookie(response)
    return response
