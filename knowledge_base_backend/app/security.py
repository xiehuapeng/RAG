from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets


def hash_password(password: str) -> str:
    # Persist salt and derived key together so one field is enough for verification.
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return base64.b64encode(salt + digest).decode("ascii")


def verify_password(password: str, encoded: str) -> bool:
    raw = base64.b64decode(encoded.encode("ascii"))
    salt, digest = raw[:16], raw[16:]
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    # Use constant-time comparison to avoid timing leaks on password checks.
    return hmac.compare_digest(candidate, digest)


def generate_token() -> str:
    # URL-safe tokens can be sent directly in headers.
    return secrets.token_urlsafe(32)
