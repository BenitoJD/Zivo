from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field
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
from app.services.guest_session import GUEST_ID_HEADER, read_guest_id
from app.services.usage import DEMO_COOKIE

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


@router.post("/signup", response_model=AuthResponse)
def signup(
    body: SignUpRequest,
    response: Response,
    db: Session = Depends(get_db),
    zivo_demo_id: str | None = Cookie(default=None, alias=DEMO_COOKIE),
    x_zivo_guest_id: str | None = Header(default=None, alias=GUEST_ID_HEADER),
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
    db.add(user)
    db.commit()
    db.refresh(user)

    claim_guest_documents(db, user.id, read_guest_id(zivo_demo_id, x_zivo_guest_id))

    token, csrf = create_session_token(user.id)
    set_session_cookie(response, token, remember=False)
    return AuthResponse(username=user.username, csrf_token=csrf)


@router.post("/login", response_model=AuthResponse)
def login(
    body: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
    zivo_demo_id: str | None = Cookie(default=None, alias=DEMO_COOKIE),
    x_zivo_guest_id: str | None = Header(default=None, alias=GUEST_ID_HEADER),
) -> AuthResponse:
    user = authenticate_user(db, body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    claim_guest_documents(db, user.id, read_guest_id(zivo_demo_id, x_zivo_guest_id))

    token, csrf = create_session_token(user.id, remember=body.remember_me)
    set_session_cookie(response, token, remember=body.remember_me)
    return AuthResponse(username=user.username, csrf_token=csrf)


@router.get("/session", response_model=AuthResponse)
def get_session(
    user: User = Depends(get_current_user),
    zivo_session: str | None = Cookie(default=None),
) -> AuthResponse:
    if not zivo_session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return AuthResponse(username=user.username, csrf_token=csrf_from_session_token(zivo_session))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, _: None = Depends(require_csrf)) -> Response:
    response.delete_cookie("zivo_session")
    return response
