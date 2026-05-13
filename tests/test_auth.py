"""Unit tests for password hashing and JWT token creation."""
from jose import jwt
from backend.auth import verify_password, get_password_hash, create_access_token
from backend.config import settings


def test_correct_password_accepted():
    hashed = get_password_hash("StrongPassword123")
    assert verify_password("StrongPassword123", hashed)


def test_wrong_password_rejected():
    hashed = get_password_hash("StrongPassword123")
    assert not verify_password("wrongpassword", hashed)


def test_token_contains_email():
    token = create_access_token(data={"sub": "user@example.com"})
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    assert payload["sub"] == "user@example.com"
