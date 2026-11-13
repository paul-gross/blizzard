"""GitHub's refusals (package-private): how a 401 or 403 that is no rate refusal becomes an :class:`AuthRefusal`, and
any refusing answer its sanitized :class:`ForgeDiagnostic`. Shared by every GitHub adapter, so each reads GitHub's
refusals the same way; free of any HTTP library, so it reads status, headers, and body fields, never a response."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.kernel.forge_refusals import (
    AuthRefusal,
    ClassifiedRefusal,
    ForgeDiagnostic,
    sanitize_forge_text,
)

_SAML_MARKERS = ("saml enforcement", "saml sso")


@domain_model
@dataclass(frozen=True)
class CallCredential:
    """The credential a refused call carried, as its diagnostic names it: its slug (``None`` for a record naming no
    credential), binding, and effective variant."""

    slug: str | None
    binding: str
    variant: str | None


def classify_auth_refusal(
    status: int,
    headers: Mapping[str, str],
    message: str,
    *,
    documentation_url: str | None,
    operation: str,
    endpoint: str,
    repository: str | None,
    credential: CallCredential | None = None,
    withheld: bool = False,
) -> ClassifiedRefusal:
    """A 401 is an invalid credential; a 403 is an SSO refusal when ``X-GitHub-SSO`` says ``required`` or the message
    names SAML enforcement, an unsupported token when ``withheld`` — the caller found the call's capability one the
    credential's variant withholds — and a missing permission otherwise. Call it only once GitHub's rate dialect has
    declined the answer as a rate refusal."""
    diagnostic = diagnostic_of(
        status,
        headers,
        message,
        documentation_url=documentation_url,
        operation=operation,
        endpoint=endpoint,
        repository=repository,
        credential=credential,
    )
    sso = _lowered(headers).get("x-github-sso", "")
    return ClassifiedRefusal(_refusal(status, sso, diagnostic.message, withheld=withheld), diagnostic)


def diagnostic_of(
    status: int,
    headers: Mapping[str, str],
    message: str,
    *,
    documentation_url: str | None,
    operation: str,
    endpoint: str,
    repository: str | None,
    credential: CallCredential | None = None,
) -> ForgeDiagnostic:
    """A refusing GitHub answer as the hub keeps it, its text sanitized, naming ``credential`` when one is given."""
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
        credential=credential.slug if credential is not None else None,
        binding=credential.binding if credential is not None else None,
        variant=credential.variant if credential is not None else None,
    )


def _lowered(headers: Mapping[str, str]) -> dict[str, str]:
    return {name.lower(): value for name, value in headers.items()}


def _refusal(status: int, sso: str, message: str, *, withheld: bool) -> AuthRefusal:
    if status == 401:
        return AuthRefusal.CREDENTIAL_INVALID
    if sso.lower().startswith("required") or any(marker in message.lower() for marker in _SAML_MARKERS):
        return AuthRefusal.SSO_REQUIRED
    if withheld:
        return AuthRefusal.TOKEN_UNSUPPORTED
    return AuthRefusal.PERMISSION_MISSING


class GitHubRefusalReader:
    """:func:`classify_auth_refusal` as the work-source family's refusal reader."""

    def classify(
        self,
        status: int,
        headers: Mapping[str, str],
        message: str,
        *,
        documentation_url: str | None,
        operation: str,
        endpoint: str,
        repository: str | None,
    ) -> ClassifiedRefusal:
        return classify_auth_refusal(
            status,
            headers,
            message,
            documentation_url=documentation_url,
            operation=operation,
            endpoint=endpoint,
            repository=repository,
        )
