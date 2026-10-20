"""Password hashing (bcrypt). JWT helpers are added in Step 0.4."""

import bcrypt

# bcrypt only uses the first 72 bytes; bcrypt>=5 raises instead of silently truncating
_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:_BCRYPT_MAX_BYTES], bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:_BCRYPT_MAX_BYTES], password_hash.encode())
    except ValueError:  # malformed hash
        return False
