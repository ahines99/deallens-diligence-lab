"""Optional OIDC SSO with IdP-role → membership-role mapping (G48).

Config-gated: password auth remains the default and these code paths are inert unless
``OIDC_ENABLED=true``. The authorization-code flow is:

  1. ``build_authorize_url`` returns the IdP authorize URL (+ opaque ``state``) the browser is sent
     to. Endpoints are derived conventionally from the issuer (``{issuer}/authorize`` and
     ``{issuer}/token``); a production deployment should instead resolve them from the issuer's
     ``/.well-known/openid-configuration`` discovery document.
  2. ``handle_callback`` exchanges the returned ``code`` for tokens at the token endpoint (httpx —
     already a dependency for this purpose), reads the ``id_token`` claims, provisions/links the
     user by verified email, maps the IdP role claim to a membership role, and issues a normal
     revocable DealLens session (reusing ``identity_service._new_session``).

The flow enforces: a single-use, TTL-bounded ``state`` (login-CSRF protection — the callback
rejects any state it did not mint), a ``nonce`` echoed through the ``id_token`` (token-replay
protection), and REQUIRED ``iss`` / ``aud`` / ``exp`` claims (absent claims are rejected, never
skipped).

SECURITY CAVEAT — the ``id_token`` signature is NOT verified here. A production deployment MUST
verify the RS256 signature against the issuer's JWKS before trusting any claim. This is called
out explicitly so the reduced scope is never mistaken for complete.
"""
from __future__ import annotations

import base64
import json
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import settings
from src.db.base import now_utc
from src.models.deal_workflow import Organization
from src.models.identity import OrganizationMembership, User
from src.schemas.identity import SessionTokenOut
from src.services.identity_service import (
    IdentityError,
    IdentityUnauthorized,
    _new_session,
    _token_response,
)

# A membership row requires a non-null password hash; SSO users never authenticate by password, so
# this sentinel is stored instead. It is not a valid pbkdf2 encoding, so _verify_password rejects
# every password against it — password login for an SSO-provisioned user is impossible.
_SSO_NO_PASSWORD = "!oidc-sso-no-password"
_VALID_ROLES = {"owner", "admin", "member", "viewer"}
_TOKEN_TIMEOUT_SECONDS = 10.0

# Pending authorize states: state -> (monotonic expiry, expected nonce). Single-use and
# TTL-bounded so a forged or replayed callback cannot complete a login (login CSRF / session
# fixation). In-process like the auth rate limiter: the Compose deployment runs one API process;
# multi-replica deployments should back this with a shared store.
_STATE_TTL_SECONDS = 600.0
_MAX_PENDING_STATES = 5_000
_pending_states: dict[str, tuple[float, str]] = {}
_pending_lock = threading.Lock()


def _remember_state(state: str, nonce: str) -> None:
    now = time.monotonic()
    with _pending_lock:
        expired = [key for key, (expiry, _) in _pending_states.items() if expiry <= now]
        for key in expired:
            _pending_states.pop(key, None)
        while len(_pending_states) >= _MAX_PENDING_STATES:
            # Oldest-first eviction keeps an unauthenticated flood from growing memory unbounded.
            oldest = min(_pending_states, key=lambda key: _pending_states[key][0])
            _pending_states.pop(oldest, None)
        _pending_states[state] = (now + _STATE_TTL_SECONDS, nonce)


def _consume_state(state: str | None) -> str | None:
    """Pop and return the nonce for a state this process minted; None when unknown/expired."""
    if not state:
        return None
    with _pending_lock:
        entry = _pending_states.pop(state, None)
    if entry is None:
        return None
    expiry, nonce = entry
    if expiry <= time.monotonic():
        return None
    return nonce


def _require_enabled() -> None:
    if not settings.oidc_enabled:
        # Surfaced as 404 by the router so a disabled feature is indistinguishable from absent.
        raise IdentityError("OIDC SSO is not enabled", status_code=404)
    missing = [
        name
        for name, value in (
            ("oidc_issuer", settings.oidc_issuer),
            ("oidc_client_id", settings.oidc_client_id),
            ("oidc_redirect_uri", settings.oidc_redirect_uri),
        )
        if not value
    ]
    if missing:
        raise IdentityError(
            f"OIDC is enabled but misconfigured: missing {', '.join(missing)}", status_code=500
        )


def _authorize_endpoint() -> str:
    return settings.oidc_issuer.rstrip("/") + "/authorize"


def _token_endpoint() -> str:
    return settings.oidc_issuer.rstrip("/") + "/token"


def build_authorize_url(state: str | None = None) -> tuple[str, str]:
    """Return ``(authorize_url, state)`` to redirect the browser to the IdP (G48).

    The minted state is remembered (single-use, TTL-bounded) so the callback can reject any
    state it did not issue, and a nonce is sent for the IdP to echo through the ``id_token``.
    """
    _require_enabled()
    state = state or secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    _remember_state(state, nonce)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.oidc_client_id,
            "redirect_uri": settings.oidc_redirect_uri,
            "scope": settings.oidc_scopes,
            "state": state,
            "nonce": nonce,
        }
    )
    return f"{_authorize_endpoint()}?{query}", state


def _exchange_code(code: str) -> dict[str, Any]:
    """Exchange an authorization code for the token response at the IdP token endpoint.

    Isolated so tests can monkeypatch the network round-trip and inject a canned ``id_token``.
    """
    response = httpx.post(
        _token_endpoint(),
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.oidc_redirect_uri,
            "client_id": settings.oidc_client_id,
            "client_secret": settings.oidc_client_secret,
        },
        headers={"Accept": "application/json"},
        timeout=_TOKEN_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        raise IdentityUnauthorized("OIDC token exchange failed")
    return response.json()


def _b64url_json(segment: str) -> dict[str, Any]:
    padded = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))


def _claims_from_id_token(id_token: str) -> dict[str, Any]:
    """Parse (WITHOUT verifying the signature — see module caveat) the id_token claim set."""
    parts = id_token.split(".")
    if len(parts) != 3:
        raise IdentityUnauthorized("Malformed id_token")
    try:
        return _b64url_json(parts[1])
    except (ValueError, json.JSONDecodeError) as exc:
        raise IdentityUnauthorized("Unreadable id_token claims") from exc


def _validate_claims(claims: dict[str, Any], expected_nonce: str) -> None:
    """Issuer/audience/expiry/nonce checks (NOT a signature verification — see module caveat).

    ``iss``, ``aud``, ``exp``, and ``nonce`` are REQUIRED: an id_token that omits any of them is
    rejected. Skipping absent claims would let a token minted for another client, an expired
    token, or a replayed token complete a login.
    """
    issuer = claims.get("iss")
    if not isinstance(issuer, str) or issuer.rstrip("/") != settings.oidc_issuer.rstrip("/"):
        raise IdentityUnauthorized("id_token issuer is missing or mismatched")
    audience = claims.get("aud")
    audiences = {audience} if isinstance(audience, str) else set(audience or ())
    if settings.oidc_client_id not in audiences:
        raise IdentityUnauthorized("id_token audience is missing or mismatched")
    expiry = claims.get("exp")
    if not isinstance(expiry, (int, float)) or datetime.fromtimestamp(
        expiry, tz=timezone.utc
    ) <= now_utc():
        raise IdentityUnauthorized("id_token expiry is missing or has passed")
    if claims.get("nonce") != expected_nonce:
        raise IdentityUnauthorized("id_token nonce is missing or mismatched")


def _role_map() -> dict[str, str]:
    raw = (settings.oidc_role_map or "").strip()
    if not raw:
        return {}
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(mapping, dict):
        return {}
    return {str(key): str(value) for key, value in mapping.items()}


def _mapped_role(claims: dict[str, Any]) -> str:
    """Map the IdP role claim to a membership role; unmapped/missing → 'viewer' (least privilege)."""
    raw = claims.get(settings.oidc_role_claim)
    idp_roles: list[str]
    if isinstance(raw, str):
        idp_roles = [raw]
    elif isinstance(raw, (list, tuple)):
        idp_roles = [str(item) for item in raw]
    else:
        idp_roles = []
    mapping = _role_map()
    for idp_role in idp_roles:
        mapped = mapping.get(idp_role)
        if mapped in _VALID_ROLES:
            return mapped
    return "viewer"


def _verified_email(claims: dict[str, Any]) -> str:
    email = claims.get("email")
    if not isinstance(email, str) or "@" not in email:
        raise IdentityUnauthorized("OIDC id_token is missing a usable email claim")
    # ``email_verified`` defaults to True only when the claim is absent; an explicit False is fatal.
    if claims.get("email_verified") is False:
        raise IdentityUnauthorized("OIDC email is not verified by the identity provider")
    return email.lower()


def handle_callback(
    session: Session,
    code: str,
    state: str | None = None,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> SessionTokenOut:
    """Complete the code exchange, provision/link the user, and issue a DealLens session (G48)."""
    _require_enabled()
    if not settings.oidc_organization_slug:
        raise IdentityError("OIDC_ORGANIZATION_SLUG is not configured", status_code=500)
    organization = session.scalar(
        select(Organization).where(Organization.slug == settings.oidc_organization_slug)
    )
    if organization is None:
        raise IdentityError("OIDC organization is not provisioned", status_code=500)

    expected_nonce = _consume_state(state)
    if expected_nonce is None:
        raise IdentityUnauthorized("OIDC state is missing, unknown, expired, or already used")

    tokens = _exchange_code(code)
    id_token = tokens.get("id_token")
    if not isinstance(id_token, str) or not id_token:
        raise IdentityUnauthorized("OIDC response did not include an id_token")
    claims = _claims_from_id_token(id_token)
    _validate_claims(claims, expected_nonce)

    email = _verified_email(claims)
    role = _mapped_role(claims)
    display_name = (
        claims.get("name") or claims.get("preferred_username") or email.split("@", 1)[0]
    )

    user = session.scalar(select(User).where(User.email_normalized == email))
    if user is None:
        user = User(
            email=email,
            email_normalized=email,
            display_name=str(display_name)[:200],
            password_hash=_SSO_NO_PASSWORD,
        )
        session.add(user)
        session.flush()

    membership = session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == user.id,
            OrganizationMembership.organization_id == organization.id,
        )
    )
    if membership is None:
        # Fresh link: assign the mapped role. An existing membership keeps its role (SSO does not
        # silently re-grant or downgrade an administrator on every login).
        membership = OrganizationMembership(
            user_id=user.id,
            organization_id=organization.id,
            role=role,
            status="active",
        )
        session.add(membership)
        session.flush()
    elif membership.status != "active":
        raise IdentityUnauthorized("Organization membership is suspended")

    user.last_login_at = now_utc()
    raw_token, auth_session = _new_session(
        session, user, membership, user_agent=user_agent, ip_address=ip_address
    )
    session.commit()
    return _token_response(session, raw_token, user, membership, auth_session)


__all__ = ["build_authorize_url", "handle_callback"]
