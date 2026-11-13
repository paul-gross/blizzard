"""GitHub's refusals (package-private): how a 401 or 403 that is no rate refusal becomes an :class:`AuthRefusal`, and
any refusing answer its sanitized :class:`ForgeDiagnostic`. Shared by every GitHub adapter, so each reads GitHub's
refusals the same way; free of any HTTP library, so it reads status, headers, and body fields, never a response."""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.hub.domain.kernel.forge_refusals import (
    AuthRefusal,
    ClassifiedRefusal,
    ForgeDiagnostic,
    sanitize_forge_text,
)

_SAML_MARKERS = ("saml enforcement", "saml sso")


def classify_auth_refusal(
    status: int,
    headers: Mapping[str, str],
    message: str,
    *,
    documentation_url: str | None,
    operation: str,
    endpoint: str,
    repository: str | None,
) -> ClassifiedRefusal:
    """A 401 is an invalid credential; a 403 is an SSO refusal when ``X-GitHub-SSO`` says ``required`` or the message
    names SAML enforcement, and a missing permission otherwise. Call it only once GitHub's rate dialect has declined
    the answer as a rate refusal."""
    diagnostic = diagnostic_of(
        status,
        headers,
        message,
        documentation_url=documentation_url,
        operation=operation,
        endpoint=endpoint,
        repository=repository,
    )
    sso = _lowered(headers).get("x-github-sso", "")
    return ClassifiedRefusal(_refusal(status, sso, diagnostic.message), diagnostic)


def diagnostic_of(
    status: int,
    headers: Mapping[str, str],
    message: str,
    *,
    documentation_url: str | None,
    operation: str,
    endpoint: str,
    repository: str | None,
) -> ForgeDiagnostic:
    """A refusing GitHub answer as the hub keeps it, its text sanitized."""
    lowered = _lowered(headers)
    return ForgeDiagnostic(
        status=status,
        operation=operation,
        endpoint=endpoint,
        repository=repository,
        message=sanitize_forge_text(message),
        documentation_url=sanitize_forge_text(documentation_url) if documentation_url else None,
        request_id=lowered.get("x-github-request-id") or None,
        accepted_permissions=lowered.get("x-accepted-github-permissions") or None,
    )


def _lowered(headers: Mapping[str, str]) -> dict[str, str]:
    return {name.lower(): value for name, value in headers.items()}


def _refusal(status: int, sso: str, message: str) -> AuthRefusal:
    if status == 401:
        return AuthRefusal.CREDENTIAL_INVALID
    if sso.lower().startswith("required") or any(marker in message.lower() for marker in _SAML_MARKERS):
        return AuthRefusal.SSO_REQUIRED
    return AuthRefusal.PERMISSION_MISSING
