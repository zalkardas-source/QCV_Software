"""Microsoft Entra OAuth 2.0 flow for Outlook inbox access via Microsoft Graph.

Server-side authorization-code flow using MSAL. The user-facing logic lives in
the FastAPI endpoints — this module is pure: build URL, exchange code, refresh
token. Side-effect-free aside from outbound HTTPS to Microsoft.
"""
import logging
from typing import Any

import msal
import requests

from backend.config import settings

logger = logging.getLogger(__name__)

# Minimum scopes required for reading mail + identifying the user. offline_access
# is implicit in MSAL for confidential clients — we get a refresh token automatically.
SCOPES: list[str] = ["Mail.Read", "User.Read"]

_GRAPH_ME = "https://graph.microsoft.com/v1.0/me"


def is_configured() -> bool:
    return bool(settings.microsoft_client_id and settings.microsoft_client_secret)


def _msal_client() -> msal.ConfidentialClientApplication:
    if not is_configured():
        raise RuntimeError(
            "Microsoft OAuth not configured — set MICROSOFT_CLIENT_ID and "
            "MICROSOFT_CLIENT_SECRET in .env"
        )
    authority = f"https://login.microsoftonline.com/{settings.microsoft_tenant_id}"
    return msal.ConfidentialClientApplication(
        client_id=settings.microsoft_client_id,
        client_credential=settings.microsoft_client_secret,
        authority=authority,
    )


def build_authorize_url(state: str) -> str:
    """Returns the Microsoft sign-in URL the user is redirected to."""
    return _msal_client().get_authorization_request_url(
        scopes=SCOPES,
        redirect_uri=settings.microsoft_redirect_uri,
        state=state,
        prompt="select_account",  # let user pick which Outlook account to connect
    )


def _resolve_email(access_token: str, id_token_claims: dict | None) -> str:
    """Picks the best email identifier we can find for the connected account."""
    if id_token_claims:
        email = id_token_claims.get("preferred_username") or id_token_claims.get("email")
        if email:
            return email
    # Fallback: ask Graph /me
    try:
        resp = requests.get(
            _GRAPH_ME,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if resp.ok:
            data = resp.json()
            return data.get("mail") or data.get("userPrincipalName") or "unknown"
    except requests.RequestException as e:
        logger.warning("Graph /me lookup failed: %s", e)
    return "unknown"


def exchange_code_for_tokens(code: str) -> dict[str, Any]:
    """Exchanges an authorization code for access + refresh tokens.

    Returns: {refresh_token, access_token, email_address, scopes}
    Raises ValueError on Microsoft-side errors.
    """
    result = _msal_client().acquire_token_by_authorization_code(
        code=code,
        scopes=SCOPES,
        redirect_uri=settings.microsoft_redirect_uri,
    )
    if "error" in result:
        raise ValueError(
            f"OAuth exchange failed: {result.get('error_description') or result['error']}"
        )
    refresh_token = result.get("refresh_token")
    if not refresh_token:
        raise ValueError(
            "Microsoft returned no refresh_token — the app may be missing the "
            "offline_access permission or the user denied consent."
        )

    email = _resolve_email(result["access_token"], result.get("id_token_claims"))
    scope_str = result.get("scope") or " ".join(SCOPES)
    return {
        "refresh_token": refresh_token,
        "access_token": result["access_token"],
        "email_address": email,
        "scopes": scope_str,
    }


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """Uses a stored refresh token to mint a new access token.

    Microsoft may rotate the refresh token — callers should persist the
    returned `refresh_token` if it differs.
    """
    result = _msal_client().acquire_token_by_refresh_token(refresh_token, scopes=SCOPES)
    if "error" in result:
        raise ValueError(
            f"Token refresh failed: {result.get('error_description') or result['error']}"
        )
    return {
        "access_token": result["access_token"],
        "refresh_token": result.get("refresh_token", refresh_token),
        "expires_in": result.get("expires_in", 3600),
    }
