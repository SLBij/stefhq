import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_session
from models.db import ApiKey, User

router = APIRouter(prefix="/auth", tags=["auth"])
bearer_scheme = HTTPBearer()


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode({"sub": user_id, "exp": expire}, settings.secret_key, algorithm="HS256")


def _hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _generate_api_key() -> str:
    return "sk_" + secrets.token_hex(32)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    token = credentials.credentials

    # Try JWT first
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        user_id = payload.get("sub")
        if not user_id:
            raise ValueError
        result = await session.execute(sa.select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        return user
    except (JWTError, ValueError):
        pass

    # Fall back to API key
    key_hash = _hash_api_key(token)
    result = await session.execute(sa.select(ApiKey).where(ApiKey.key_hash == key_hash))
    api_key = result.scalar_one_or_none()
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    # Update last_used_at
    api_key.last_used_at = datetime.now(timezone.utc)
    await session.commit()

    # Personal app — API keys belong to the single owner
    result = await session.execute(sa.select(User).limit(1))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No user found")
    return user


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest, session: AsyncSession = Depends(get_session)):
    result = await session.execute(sa.select(User).where(User.email == request.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return TokenResponse(access_token=create_access_token(str(user.id)))


class ApiKeyResponse(BaseModel):
    key: str
    name: str


@router.post("/api-key", response_model=ApiKeyResponse)
async def create_api_key(
    name: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    key = _generate_api_key()
    session.add(ApiKey(name=name, key_hash=_hash_api_key(key), workspace_permissions=[]))
    await session.commit()
    return ApiKeyResponse(key=key, name=name)
