"""Unit tests for Microsoft OAuth integration."""
from unittest.mock import patch, MagicMock

import pytest

from backend import oauth_microsoft, crypto


# ── crypto helpers ───────────────────────────────────────────────────────────

def test_crypto_roundtrip():
    plaintext = "secret-refresh-token-xyz-12345"
    encrypted = crypto.encrypt(plaintext)
    assert encrypted != plaintext
    assert crypto.decrypt(encrypted) == plaintext


def test_crypto_different_calls_produce_different_ciphertext():
    """Fernet is non-deterministic — same plaintext encrypted twice differs."""
    plaintext = "abc"
    a = crypto.encrypt(plaintext)
    b = crypto.encrypt(plaintext)
    assert a != b
    assert crypto.decrypt(a) == crypto.decrypt(b) == plaintext


def test_crypto_rejects_tampered_token():
    from cryptography.fernet import InvalidToken
    encrypted = crypto.encrypt("hello")
    tampered = encrypted[:-1] + ("A" if encrypted[-1] != "A" else "B")
    with pytest.raises(InvalidToken):
        crypto.decrypt(tampered)


# ── is_configured ────────────────────────────────────────────────────────────

def test_is_configured_false_without_credentials():
    with patch.object(oauth_microsoft, "settings") as mock_settings:
        mock_settings.microsoft_client_id = None
        mock_settings.microsoft_client_secret = None
        assert oauth_microsoft.is_configured() is False


def test_is_configured_true_with_credentials():
    with patch.object(oauth_microsoft, "settings") as mock_settings:
        mock_settings.microsoft_client_id = "client-id"
        mock_settings.microsoft_client_secret = "client-secret"
        assert oauth_microsoft.is_configured() is True


def test_msal_client_raises_when_not_configured():
    with patch.object(oauth_microsoft, "settings") as mock_settings:
        mock_settings.microsoft_client_id = None
        mock_settings.microsoft_client_secret = None
        with pytest.raises(RuntimeError, match="not configured"):
            oauth_microsoft._msal_client()


# ── build_authorize_url ──────────────────────────────────────────────────────

def test_build_authorize_url_uses_msal():
    fake_client = MagicMock()
    fake_client.get_authorization_request_url.return_value = "https://login.microsoftonline.com/..."
    with patch.object(oauth_microsoft, "_msal_client", return_value=fake_client):
        url = oauth_microsoft.build_authorize_url(state="abc")

    assert url.startswith("https://login.microsoftonline.com/")
    fake_client.get_authorization_request_url.assert_called_once()
    kwargs = fake_client.get_authorization_request_url.call_args.kwargs
    assert kwargs["state"] == "abc"
    assert "Mail.Read" in kwargs["scopes"]


# ── exchange_code_for_tokens ─────────────────────────────────────────────────

def _good_token_response(email: str = "user@example.com") -> dict:
    return {
        "access_token": "access-token-1",
        "refresh_token": "refresh-token-1",
        "id_token_claims": {"preferred_username": email},
        "scope": "Mail.Read User.Read",
    }


def test_exchange_returns_tokens_and_email():
    fake_client = MagicMock()
    fake_client.acquire_token_by_authorization_code.return_value = _good_token_response()

    with patch.object(oauth_microsoft, "_msal_client", return_value=fake_client):
        result = oauth_microsoft.exchange_code_for_tokens("auth-code")

    assert result["refresh_token"] == "refresh-token-1"
    assert result["access_token"] == "access-token-1"
    assert result["email_address"] == "user@example.com"
    assert "Mail.Read" in result["scopes"]


def test_exchange_raises_on_microsoft_error():
    fake_client = MagicMock()
    fake_client.acquire_token_by_authorization_code.return_value = {
        "error": "invalid_grant",
        "error_description": "Authorization code expired",
    }
    with patch.object(oauth_microsoft, "_msal_client", return_value=fake_client):
        with pytest.raises(ValueError, match="Authorization code expired"):
            oauth_microsoft.exchange_code_for_tokens("auth-code")


def test_exchange_raises_when_no_refresh_token():
    """If offline_access wasn't granted, Microsoft returns no refresh_token."""
    fake_client = MagicMock()
    response = _good_token_response()
    del response["refresh_token"]
    fake_client.acquire_token_by_authorization_code.return_value = response

    with patch.object(oauth_microsoft, "_msal_client", return_value=fake_client):
        with pytest.raises(ValueError, match="offline_access"):
            oauth_microsoft.exchange_code_for_tokens("auth-code")


def test_exchange_falls_back_to_graph_for_email():
    """When id_token_claims has no email, query Microsoft Graph /me."""
    fake_client = MagicMock()
    response = _good_token_response()
    response["id_token_claims"] = {}  # no preferred_username/email
    fake_client.acquire_token_by_authorization_code.return_value = response

    fake_resp = MagicMock()
    fake_resp.ok = True
    fake_resp.json.return_value = {"mail": "graph-fallback@example.com"}

    with patch.object(oauth_microsoft, "_msal_client", return_value=fake_client), \
         patch.object(oauth_microsoft.requests, "get", return_value=fake_resp):
        result = oauth_microsoft.exchange_code_for_tokens("auth-code")

    assert result["email_address"] == "graph-fallback@example.com"


# ── refresh_access_token ─────────────────────────────────────────────────────

def test_refresh_access_token_success():
    fake_client = MagicMock()
    fake_client.acquire_token_by_refresh_token.return_value = {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_in": 3600,
    }
    with patch.object(oauth_microsoft, "_msal_client", return_value=fake_client):
        result = oauth_microsoft.refresh_access_token("old-refresh")

    assert result["access_token"] == "new-access"
    assert result["refresh_token"] == "new-refresh"
    assert result["expires_in"] == 3600


def test_refresh_access_token_preserves_old_refresh_when_not_rotated():
    """Microsoft sometimes returns no new refresh_token — keep the old one."""
    fake_client = MagicMock()
    fake_client.acquire_token_by_refresh_token.return_value = {
        "access_token": "new-access",
        "expires_in": 3600,
        # no refresh_token in response
    }
    with patch.object(oauth_microsoft, "_msal_client", return_value=fake_client):
        result = oauth_microsoft.refresh_access_token("old-refresh")

    assert result["refresh_token"] == "old-refresh"


def test_refresh_raises_on_microsoft_error():
    fake_client = MagicMock()
    fake_client.acquire_token_by_refresh_token.return_value = {
        "error": "invalid_grant",
        "error_description": "The refresh token has expired",
    }
    with patch.object(oauth_microsoft, "_msal_client", return_value=fake_client):
        with pytest.raises(ValueError, match="refresh token has expired"):
            oauth_microsoft.refresh_access_token("dead-token")
