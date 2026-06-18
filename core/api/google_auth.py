import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from config import settings
from database import get_session
from integrations.google import SCOPES
from models.db import GoogleToken, User
from workers.arq_pool import get_pool

router = APIRouter(tags=["oauth"])

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_STATE_TTL = 600  # 10 minutes


@router.get("/oauth/google/authorize")
async def google_authorize(user: User = Depends(get_current_user)):
    state = str(uuid.uuid4())
    pool = await get_pool()
    await pool.set(f"oauth_state:{state}", str(user.id), ex=_STATE_TTL)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return {"url": f"{_AUTH_URL}?{urlencode(params)}"}


@router.get("/oauth/google/callback")
async def google_callback(
    code: str,
    state: str,
    session: AsyncSession = Depends(get_session),
):
    pool = await get_pool()
    raw = await pool.get(f"oauth_state:{state}")
    if not raw:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    await pool.delete(f"oauth_state:{state}")

    user_id = uuid.UUID(raw.decode() if isinstance(raw, bytes) else raw)

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(_TOKEN_URL, data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.google_redirect_uri,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
        })
        resp.raise_for_status()
        tokens = resp.json()

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=tokens["expires_in"] - 60)

    result = await session.execute(
        sa.select(GoogleToken).where(GoogleToken.user_id == user_id)
    )
    token = result.scalar_one_or_none()
    if token:
        token.access_token = tokens["access_token"]
        if "refresh_token" in tokens:
            token.refresh_token = tokens["refresh_token"]
        token.expires_at = expires_at
        token.scopes = tokens.get("scope", "").split()
        token.updated_at = datetime.now(timezone.utc)
    else:
        token = GoogleToken(
            user_id=user_id,
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            expires_at=expires_at,
            scopes=tokens.get("scope", "").split(),
        )
        session.add(token)
    await session.commit()

    return RedirectResponse(url=f"{settings.frontend_url}/google-connect?status=success")


@router.get("/oauth/google/status")
async def google_status(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        sa.select(GoogleToken.id).where(GoogleToken.user_id == user.id)
    )
    connected = result.scalar_one_or_none() is not None
    return {"connected": connected}
