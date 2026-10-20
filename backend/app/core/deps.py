"""Shared FastAPI dependencies and their annotated aliases."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import InvalidTokenError, decode_access_token
from app.db.models import User
from app.db.session import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbSession = Annotated[AsyncSession, Depends(get_db)]


def credentials_exception(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)], db: DbSession, settings: SettingsDep
) -> User:
    if not token:
        raise credentials_exception()
    try:
        user_id = decode_access_token(token, settings)
    except InvalidTokenError as exc:
        raise credentials_exception("Invalid or expired token") from exc
    user = await db.get(User, user_id)
    if user is None:
        raise credentials_exception("User no longer exists")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
