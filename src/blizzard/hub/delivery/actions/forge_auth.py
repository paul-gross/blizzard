"""The ``forge-auth`` escalation of a credential the forge refused — one wording for a refused build and a refused
call alike."""

from __future__ import annotations

from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.hub.domain.actions.effects import ActionEscalation, ForgeAuthReason
from blizzard.hub.domain.config.repositories import RepositoryId, ResolvedRepository
from blizzard.hub.domain.kernel.forge_refusals import ForgeDiagnostic, refusal_remedy, refusal_sentence
from blizzard.hub.forge.landing import OPERATION_CAPABILITY, ForgeAuthRefused


def credential_kind(binding: str, variant: str | None) -> str:
    """A credential's binding and any effective variant, as an operator reads them."""
    return binding if variant is None else f"{binding}, {variant}"


def forge_auth_escalation(
    exc: ForgeAuthRefused, *, repo: str, forge_label: str, record: ResolvedRepository, chunk_id: str
) -> ActionEscalation:
    """The ``forge-auth`` escalation of a credential the forge refused for ``repo``, acting as ``record`` on the forge
    labelled ``forge_label``: what was refused and why, the forge's own sanitized words, the credential or secret the
    call carried, and what to do about it — then, for a call carrying a credential, the commands that check it for the
    repository record, by its slug, and requeue chunk ``chunk_id`` once it is fixed."""
    diagnostic = exc.diagnostic
    needed = OPERATION_CAPABILITY.get(diagnostic.operation)
    capability = str(needed) if needed is not None else None
    lines = [
        f"{repo} on forge {forge_label}: {_operation_sentence(diagnostic)} was refused: "
        f"{refusal_sentence(exc.refusal)}.",
        f"GitHub said: {_forge_said(diagnostic)}.",
        f"{_carried(diagnostic, record)}. {refusal_remedy(exc.refusal, diagnostic, capability=capability)}",
    ]
    if diagnostic.credential is not None:
        lines.append(
            f"Then run: blizzard hub credential check {diagnostic.credential} --repo {record.slug or record.record}; "
            f"blizzard hub chunk requeue {chunk_id}."
        )
    detail = "\n".join(lines)
    reason = ForgeAuthReason(
        refusal=exc.refusal,
        diagnostic=diagnostic,
        repository=RepositoryId(record.record),
        forge=record.forge_id,
    )
    return ActionEscalation(EscalationCause.FORGE_AUTH, detail, auth=reason)


def _carried(diagnostic: ForgeDiagnostic, record: ResolvedRepository) -> str:
    """The credential the refused call carried, as its binding and any variant; the secret when it names none."""
    if diagnostic.credential is None:
        return f"Secret {record.secret_name}"
    assert diagnostic.binding is not None  # a diagnostic naming a credential names its binding
    return f"Credential {diagnostic.credential} ({credential_kind(diagnostic.binding, diagnostic.variant)})"


def _operation_sentence(diagnostic: ForgeDiagnostic) -> str:
    return f"the `{diagnostic.operation}` call ({diagnostic.endpoint})"


def _forge_said(diagnostic: ForgeDiagnostic) -> str:
    """The forge's quoted message and the answer's status and request id; a request id it did not send is left
    out."""
    answer = f"HTTP {diagnostic.status}"
    if diagnostic.request_id is not None:
        answer += f", request {diagnostic.request_id}"
    return f'"{diagnostic.message}" ({answer})'
