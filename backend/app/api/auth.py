from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Account as User
from app.services.auth import (
    authenticate_user,
    create_session_token,
    csrf_from_session_token,
    get_current_user,
    hash_password,
    require_csrf,
    set_session_cookie,
    validate_password,
    validate_username,
)
from app.services.guest import claim_guest_documents
from app.services.guest_session import publish_guest_id, read_guest_id_from_cookie
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


class AuthResponse(BaseModel):
    username: str
    csrf_token: str
    is_admin: bool = False


@router.post("/guest")
def mint_guest_session(
    response: Response,
    zivo_demo_id: str | None = Cookie(default=None, alias=DEMO_COOKIE),
) -> dict[str, str]:
    """Mint or refresh the anonymous guest cookie without listing sources."""
    guest_id = ensure_demo_cookie(response, read_guest_id_from_cookie(zivo_demo_id))
    publish_guest_id(response, guest_id)
    return {"guest_id": guest_id}


@router.post("/signup", response_model=AuthResponse)
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

    from app.models import Account as User

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

    claim_guest_documents(db, user.id, read_guest_id_from_cookie(zivo_demo_id))

    token, csrf = create_session_token(user.id, int(getattr(user, "session_version", 0) or 0))
    set_session_cookie(response, token, remember=False)
    return AuthResponse(username=user.username, csrf_token=csrf, is_admin=user.is_admin)


@router.post("/login", response_model=AuthResponse)
def login(
    body: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
    zivo_demo_id: str | None = Cookie(default=None, alias=DEMO_COOKIE),
) -> AuthResponse:
    user = authenticate_user(db, body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    claim_guest_documents(db, user.id, read_guest_id_from_cookie(zivo_demo_id))

    token, csrf = create_session_token(
        user.id, int(getattr(user, "session_version", 0) or 0), remember=body.remember_me
    )
    set_session_cookie(response, token, remember=body.remember_me)
    return AuthResponse(username=user.username, csrf_token=csrf, is_admin=user.is_admin)


@router.get("/session", response_model=AuthResponse)
def get_session(
    user: User = Depends(get_current_user),
    zivo_session: str | None = Cookie(default=None),
) -> AuthResponse:
    if not zivo_session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return AuthResponse(username=user.username, csrf_token=csrf_from_session_token(zivo_session), is_admin=user.is_admin)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(require_csrf),
) -> Response:
    user.session_version = int(getattr(user, "session_version", 0) or 0) + 1
    db.commit()
    response.delete_cookie("zivo_session")
    return response
