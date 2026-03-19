"""JWT authentication — user model, token issuance, and middleware."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pdf_agent.config import settings
from pdf_agent.db import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# Minimal JWT implementation (no PyJWT dependency)
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return urlsafe_b64decode(s)


def _jwt_encode(payload: dict[str, Any], secret: str, algorithm: str = "HS256") -> str:
    header = {"alg": algorithm, "typ": "JWT"}
    segments = [
        _b64url_encode(json.dumps(header, separators=(",", ":")).encode()),
        _b64url_encode(json.dumps(payload, separators=(",", ":")).encode()),
    ]
    signing_input = ".".join(segments).encode()
    if algorithm == "HS256":
        sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    else:
        raise ValueError(f"Unsupported algorithm: {algorithm}")
    segments.append(_b64url_encode(sig))
    return ".".join(segments)


def _jwt_decode(token: str, secret: str, algorithm: str = "HS256") -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid token")
    signing_input = f"{parts[0]}.{parts[1]}".encode()
    if algorithm == "HS256":
        expected_sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    else:
        raise ValueError(f"Unsupported algorithm: {algorithm}")
    actual_sig = _b64url_decode(parts[2])
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise ValueError("Invalid signature")
    payload = json.loads(_b64url_decode(parts[1]))
    if payload.get("exp") and payload["exp"] < time.time():
        raise ValueError("Token expired")
    return payload


def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": int(time.time()) + settings.jwt_expire_hours * 3600,
        "iat": int(time.time()),
    }
    return _jwt_encode(payload, settings.jwt_secret, settings.jwt_algorithm)


def verify_token(token: str) -> dict[str, Any]:
    return _jwt_decode(token, settings.jwt_secret, settings.jwt_algorithm)


# ---------------------------------------------------------------------------
# Password hashing (SHA-256 + salt — no bcrypt dependency)
# ---------------------------------------------------------------------------

def _hash_password(password: str, salt: str | None = None) -> str:
    if salt is None:
        salt = uuid.uuid4().hex[:16]
    h = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}${h}"


def _verify_password(password: str, hashed: str) -> bool:
    salt = hashed.split("$")[0]
    return hmac.compare_digest(_hash_password(password, salt), hashed)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest, session: AsyncSession = Depends(get_session)):
    """Register a new user account."""
    if not settings.jwt_secret:
        raise HTTPException(status_code=501, detail="User auth not enabled (JWT_SECRET not set)")

    from pdf_agent.db.models import User
    existing = await session.execute(select(User).where(User.email == req.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        id=uuid.uuid4(),
        email=req.email,
        password_hash=_hash_password(req.password),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    token = create_access_token(str(user.id), user.email)
    return TokenResponse(access_token=token, user_id=str(user.id), email=user.email)


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, session: AsyncSession = Depends(get_session)):
    """Authenticate and obtain a JWT token."""
    if not settings.jwt_secret:
        raise HTTPException(status_code=501, detail="User auth not enabled (JWT_SECRET not set)")

    from pdf_agent.db.models import User
    result = await session.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()

    if not user or not _verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(str(user.id), user.email)
    return TokenResponse(access_token=token, user_id=str(user.id), email=user.email)


@router.get("/me")
async def me(user=Depends(lambda: None)):
    """Get current user info — placeholder, requires JWT middleware to inject user."""
    raise HTTPException(status_code=501, detail="Not implemented without active JWT middleware")
