"""Password hashing (bcrypt) and JWT access tokens."""

import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
from jose import JWTError, jwt

from app.core.config import Settings

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"

# bcrypt only uses the first 72 bytes; bcrypt>=5 raises instead of silently truncating
_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:_BCRYPT_MAX_BYTES], bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:_BCRYPT_MAX_BYTES], password_hash.encode())
    except ValueError:  # malformed hash
        return False


# Compared against when the email is unknown, so login takes the same time either way
DUMMY_PASSWORD_HASH = hash_password("timing-equaliser")


class InvalidTokenError(Exception):
    pass


def create_access_token(user_id: uuid.UUID, settings: Settings) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "type": ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    token: str = jwt.encode(claims, _signing_key(settings), algorithm=JWT_ALGORITHM)
    return token


def decode_access_token(token: str, settings: Settings) -> uuid.UUID:
    """Return the user id from a valid access token, else raise InvalidTokenError."""
    try:
        claims = jwt.decode(token, _signing_key(settings), algorithms=[JWT_ALGORITHM])
        if claims.get("type") != ACCESS_TOKEN_TYPE:
            raise InvalidTokenError("not an access token")
        return uuid.UUID(claims["sub"])
    except (JWTError, KeyError, ValueError) as exc:
        raise InvalidTokenError(str(exc)) from exc


def _signing_key(settings: Settings) -> str:
    if not settings.secret_key:
        raise RuntimeError("SECRET_KEY is not set")
    return settings.secret_key
