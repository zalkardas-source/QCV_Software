"""Symmetric encryption for OAuth refresh tokens (and any other at-rest secrets).

The Fernet key is derived from the application's JWT_SECRET via PBKDF2 so we
don't need an additional environment variable. Consequence: rotating
JWT_SECRET invalidates all stored refresh tokens — users must re-connect their
accounts. This is acceptable because token rotation should be a rare event and
the trade-off (one less secret to manage) is worth it.
"""
import base64
import hashlib

from cryptography.fernet import Fernet

from backend.config import settings


_SALT = b"qcv-oauth-token-v1"  # fixed salt — key uniqueness comes from jwt_secret


def _fernet() -> Fernet:
    """Derives a Fernet key from settings.jwt_secret."""
    key_bytes = hashlib.pbkdf2_hmac(
        "sha256",
        settings.jwt_secret.encode("utf-8"),
        _SALT,
        iterations=100_000,
        dklen=32,
    )
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(fernet_key)


def encrypt(plaintext: str) -> str:
    """Encrypts a string and returns it as a URL-safe ASCII token."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    """Decrypts a token produced by `encrypt`. Raises if tampered or wrong key."""
    return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
