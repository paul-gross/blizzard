"""Why a forge refused a credential, and the sanitized account of the answer it refused with.

Every hub package names this vocabulary — the landing seam and its adapters, the work-source family, and the
forge-status pass — so it lives in the kernel rather than beside any one forge adapter. It imports no HTTP library: an
adapter classifies its forge's answer into an :class:`AuthRefusal` and a :class:`ForgeDiagnostic`, and raises its own
exception carrying them. No text here carries a token, a JWT, a private key, or an ``Authorization`` value —
:func:`sanitize_forge_text` strips each from what the forge said before it is kept."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from enum import StrEnum

from blizzard.foundation.roles import domain_model

#: The longest text :func:`sanitize_forge_text` answers.
SANITIZED_TEXT_MAX = 500
#: What stands where a secret was.
REDACTED = "[redacted]"


class AuthRefusal(StrEnum):
    """Why the forge refused the credential a call carried."""

    CREDENTIAL_INVALID = "credential-invalid"
    PERMISSION_MISSING = "permission-missing"
    TOKEN_UNSUPPORTED = "token-unsupported"
    SSO_REQUIRED = "sso-required"


@domain_model
@dataclass(frozen=True)
class ForgeDiagnostic:
    """The forge's refusing answer, as the hub keeps it. ``operation`` is the seam method or work-source call that was
    refused; ``endpoint`` its method and route template, never a filled-in path or query; ``message`` is the forge's
    own words, sanitized. ``credential``, ``binding`` and ``variant`` name the credential the call carried — its slug,
    its binding and its effective variant — and are ``None`` where the caller named none. Each field the forge's answer
    does not carry is ``None``."""

    status: int
    operation: str
    endpoint: str
    repository: str | None
    message: str
    documentation_url: str | None
    request_id: str | None
    accepted_permissions: str | None
    credential: str | None = None
    binding: str | None = None
    variant: str | None = None


@domain_model
@dataclass(frozen=True)
class ClassifiedRefusal:
    """A forge's 401 or 403 answer, classified."""

    refusal: AuthRefusal
    diagnostic: ForgeDiagnostic


_SENTENCES = {
    AuthRefusal.CREDENTIAL_INVALID: "the token is wrong, expired, or revoked",
    AuthRefusal.PERMISSION_MISSING: "the token lacks a permission the call needs",
    AuthRefusal.TOKEN_UNSUPPORTED: "this kind of token cannot make the call",
    AuthRefusal.SSO_REQUIRED: "the token is not authorized for the organization's SSO",
}


def refusal_sentence(refusal: AuthRefusal) -> str:
    """The refusal in a clause an operator reads, without a closing period."""
    return _SENTENCES[refusal]


def refusal_remedy(refusal: AuthRefusal, diagnostic: ForgeDiagnostic, *, capability: str | None = None) -> str:
    """What the operator does about the refusal, as one sentence with its closing period. ``capability`` is what the
    refused call needed, as the caller maps it from the diagnostic's operation."""
    match refusal:
        case AuthRefusal.CREDENTIAL_INVALID:
            return "Replace the token, or check the App's installation."
        case AuthRefusal.PERMISSION_MISSING:
            granted = diagnostic.accepted_permissions or "the permission the call needs"
            return f"Grant {granted} to the token or the App installation."
        case AuthRefusal.TOKEN_UNSUPPORTED:
            needed = f"`{capability}`" if capability is not None else "what the call needs"
            return f"Use a classic token or a GitHub App, or a landing policy that does not need {needed}."
        case AuthRefusal.SSO_REQUIRED:
            return "Authorize the token for the organization's SSO."


def forge_auth_refused_detail(
    classified: ClassifiedRefusal,
    *,
    forge_id: str,
    chunk_id: str | None = None,
    repository_id: str | None = None,
    work_source_id: str | None = None,
) -> dict[str, object]:
    """The ``forge-auth-refused`` event's detail: who was refused, why, and the forge's sanitized answer."""
    return {
        "chunk_id": chunk_id,
        "repository_id": repository_id,
        "work_source_id": work_source_id,
        "forge_id": forge_id,
        "refusal": str(classified.refusal),
        "diagnostic": asdict(classified.diagnostic),
    }


_WHITESPACE = re.compile(r"\s+")
# A PEM block runs to its END line, or to the end of the text when it has none.
_PEM = re.compile(r"-----BEGIN[^-]*-----.*?(?:-----END[^-]*-----|$)", re.DOTALL)
_AUTHORIZATION = re.compile(
    r"(authorization[\"']?\s*[:=]\s*[\"']?\s*)(?!\[redacted\])(?:(?:bearer|token|basic)\s+)?[^\s,;\"']+", re.IGNORECASE
)
_TOKEN = re.compile(r"gh[pousr]_\w+|github_pat_\w+")
_JWT = re.compile(r"eyJ[\w-]*\.[\w-]*\.[\w-]*")


def sanitize_forge_text(text: str) -> str:
    """``text`` with every GitHub token, JWT, PEM block, and ``Authorization`` value replaced by :data:`REDACTED`,
    its whitespace collapsed, and cut to at most :data:`SANITIZED_TEXT_MAX` characters. The redactions repeat until
    none applies, so no secret survives as the join of what surrounded another."""
    cleaned = _WHITESPACE.sub(" ", text).strip()
    while True:
        redacted = _PEM.sub(REDACTED, cleaned)
        redacted = _AUTHORIZATION.sub(lambda match: f"{match.group(1)}{REDACTED}", redacted)
        redacted = _JWT.sub(REDACTED, _TOKEN.sub(REDACTED, redacted))
        if redacted == cleaned:
            break
        cleaned = redacted
    if len(cleaned) > SANITIZED_TEXT_MAX:
        return cleaned[: SANITIZED_TEXT_MAX - 1] + "…"
    return cleaned
