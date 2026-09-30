"""Registration, login, and the current-user dependency.

Passwords are hashed with bcrypt and are never stored, returned, or logged in
plaintext. Sessions are stateless JWTs (HS256) presented as
``Authorization: Bearer <token>``.

    POST /auth/register  201 + public user record
    POST /auth/login     200 + access token
    GET  /auth/me        200 + the authenticated user
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import bcrypt
import jwt
from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from Main.Database_Manager import User, get_db
from Main.Errors import conflict, unauthenticated

# 47 bytes, comfortably over the 32-byte minimum PyJWT recommends for HS256.
# Override with a real secret via the JWT_SECRET environment variable.
JWT_SECRET: str = os.getenv(
    "JWT_SECRET", "dev-only-insecure-secret-change-me-in-production"
)
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))

# bcrypt refuses inputs longer than 72 bytes, so the schemas cap password
# length and we never silently truncate.
BCRYPT_MAX_BYTES = 72

router = APIRouter(prefix="/auth", tags=["Authentication"])
bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(plain_password: str) -> str:
    """Return a salted bcrypt hash suitable for storage."""
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Check a candidate password against a stored hash."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), password_hash.encode("utf-8")
        )
    except ValueError:
        # A malformed hash in the database must fail closed, not crash.
        return False


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """Throwaway hash so a missing account costs the same as a bad password."""
    return hash_password("swibit-dummy-password")


def create_access_token(user: User) -> str:
    """Issue a signed, time-limited JWT identifying ``user``."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXPIRE_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the caller from the bearer token, or reject with 401."""
    if credentials is None or not credentials.credentials:
        raise unauthenticated("Missing bearer token.")

    try:
        payload = jwt.decode(
            credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM]
        )
    except jwt.ExpiredSignatureError:
        raise unauthenticated("Access token has expired.") from None
    except jwt.PyJWTError:
        raise unauthenticated("Access token is invalid.") from None

    subject = payload.get("sub")
    if not subject or not str(subject).isdigit():
        raise unauthenticated("Access token is missing a valid subject.")

    user = db.get(User, int(subject))
    if user is None:
        raise unauthenticated("Account no longer exists.")
    return user


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    """Everything required to create an account."""

    user_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=BCRYPT_MAX_BYTES)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=BCRYPT_MAX_BYTES)


class UserResponse(BaseModel):
    """Public user record. Deliberately has no password field at all."""

    model_config = {"from_attributes": True}

    user_id: int
    user_name: str
    email: EmailStr
    user_privilege: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
@router.post("/register", status_code=201, response_model=UserResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> User:
    """Create an account. A duplicate email is a 409 conflict."""
    email = payload.email.lower()
    if db.scalar(select(User).where(User.email == email)) is not None:
        raise conflict("email_already_registered", "That email is already registered.")

    user = User(
        user_name=payload.user_name,
        email=email,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Lost a race against a concurrent registration of the same email.
        db.rollback()
        raise conflict(
            "email_already_registered", "That email is already registered."
        ) from None
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Exchange valid credentials for a bearer token."""
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None:
        # Burn the same time as a real comparison so response latency does
        # not reveal whether the email exists.
        bcrypt.checkpw(payload.password.encode("utf-8"), _dummy_hash().encode("utf-8"))
        raise unauthenticated("Invalid email or password.")
    if not verify_password(payload.password, user.password_hash):
        raise unauthenticated("Invalid email or password.")

    return TokenResponse(
        access_token=create_access_token(user),
        expires_in=JWT_EXPIRE_MINUTES * 60,
    )


@router.get("/me", response_model=UserResponse)
def read_current_user(current_user: User = Depends(get_current_user)) -> User:
    """Return the authenticated caller's own record."""
    return current_user

