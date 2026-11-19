"""Human-plane credential record reads and authoring verbs. Writes go only through ``ConfigAuthoring``; the actor and
door come from the request's :class:`ChangeContext`."""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Depends, Header, status

from blizzard.foundation.credential_check import UseKind
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import problems, problems_admin
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.credential_params import PathCredential
from blizzard.hub.api.deps import Services
from blizzard.hub.api.door import RequestDoor, change_context
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.api.user_names import user_names
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.auth.permissions import Permission
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.config.changes import RecordRef
from blizzard.hub.domain.config.credential_bindings import CREDENTIAL_BINDINGS, bindings_for_kind
from blizzard.hub.domain.config.credential_resolution import CredentialCapabilityMissing
from blizzard.hub.domain.config.credentials import (
    ConfiguredCredential,
    CredentialAuthorityTaken,
    CredentialDraft,
    CredentialEdit,
    CredentialInUse,
    CredentialNotUsedBy,
    CredentialSlugTaken,
    CredentialUnavailable,
)
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeId
from blizzard.hub.domain.kernel.forge_refusals import ForgeDiagnostic
from blizzard.hub.forge.readiness import CredentialCheck, OperationResult
from blizzard.wire.credential import (
    CredentialBindingView,
    CredentialCheckRequest,
    CredentialCheckView,
    CredentialCreateRequest,
    CredentialDocument,
    CredentialListItem,
    CredentialPatchRequest,
    CredentialReferenceView,
    CredentialSettingView,
    CredentialsListView,
    CredentialSummary,
    ForgeBindingsView,
    ForgeDiagnosticView,
    OperationResultView,
    UseResultView,
)
from blizzard.wire.record_referrer import RecordReferrer

router = APIRouter(
    prefix="/api/credentials",
    tags=["credentials"],
    dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)],
)

#: Each request field a patch may carry, with the :class:`CredentialEdit` field it sets.
_EDIT_FIELDS = {
    "slug": "slug",
    "name": "name",
    "secret": "secret_name",
    "settings": "settings",
    "forge": "forge_id",
    "binding": "binding",
}


def _summary(record: ConfiguredCredential, forge: ConfiguredForge, names: Mapping[str, str]) -> CredentialSummary:
    fields = record.fields
    return CredentialSummary(
        credential_id=record.credential_id,
        slug=record.slug,
        name=record.name,
        forge_id=forge.forge_id,
        forge_slug=forge.slug,
        forge_name=forge.name,
        binding=fields.binding,
        secret_name=fields.secret_name,
        settings=dict(fields.settings),
        authority=record.authority,
        revision=record.revision,
        created_at=iso_utc(record.created_at),
        created_by=record.created_by,
        created_by_name=names.get(record.created_by),
        retired=record.retired,
    )


def _shown(record: ConfiguredCredential, services: TenantServices) -> CredentialSummary:
    """``record``'s summary, with the forge it names."""
    forge = services.forge_records.get(record.fields.forge_id)
    assert forge is not None
    return _summary(record, forge, user_names(services, [record.created_by]))


def _referrers(refs: list[RecordRef]) -> list[RecordReferrer]:
    return [RecordReferrer(kind=r.kind.value, key=r.key, label=r.label) for r in refs]


def _unprocessable(exc: ConfigFieldError) -> ProblemHTTPException:
    if isinstance(exc, CredentialCapabilityMissing):
        return ProblemHTTPException(
            partial(
                problems_admin.credential_capability_missing_problem,
                str(exc),
                _referrers(exc.referrers),
                list(exc.missing),
            )
        )
    return ProblemHTTPException(
        partial(problems_admin.config_field_invalid_problem, "body", exc.field, exc.message, exc.code)
    )


def _conflict(exc: Exception) -> ProblemHTTPException:
    if isinstance(exc, CredentialInUse):
        return ProblemHTTPException(
            partial(problems_admin.credential_in_use_problem, str(exc), _referrers(exc.referrers))
        )
    if isinstance(exc, CredentialAuthorityTaken):
        return ProblemHTTPException(
            partial(problems_admin.credential_authority_taken_problem, str(exc), exc.holder, exc.authority)
        )
    if isinstance(exc, CredentialSlugTaken):
        return ProblemHTTPException(partial(problems_admin.credential_slug_taken_problem, str(exc), exc.slug))
    return ProblemHTTPException(partial(problems.revision_conflict_problem, str(exc)))


@router.get("", response_model=CredentialsListView, dependencies=[Depends(require(Permission.FLEET_VIEW))])
def list_credentials(services: Services, include_retired: bool = False) -> CredentialsListView:
    """Every stored credential by slug, retired ones hidden unless `include_retired`, each with the count of enabled
    repositories and work sources naming it; and every binding a credential may name, with the kind of forge it
    authenticates against and each setting's choices; and, for each enabled forge, the bindings a credential on it may
    name."""
    records = services.credential_records.list_all(include_retired=include_retired)
    names = user_names(services, (r.created_by for r in records))
    forges = services.forge_records.get_many(list({r.fields.forge_id for r in records}))
    referrers = services.credential_references.referrers_of([r.credential_id for r in records])
    return CredentialsListView(
        credentials=[
            CredentialListItem(
                **_summary(r, forges[r.fields.forge_id], names).model_dump(),
                referrer_count=len(referrers[r.credential_id]),
            )
            for r in records
        ],
        bindings=[
            CredentialBindingView(
                name=binding.name,
                forge_kind=binding.forge_kind,
                settings=[
                    CredentialSettingView(key=key, kind=kind, choices=list(binding.setting_choices.get(key, ())))
                    for key, kind in binding.setting_kinds.items()
                ],
            )
            for binding in CREDENTIAL_BINDINGS.values()
        ],
        forge_bindings=[
            ForgeBindingsView(forge_id=forge.forge_id, bindings=list(bindings_for_kind(forge.kind)))
            for forge in services.forge_records.list_all(include_retired=False)
        ],
    )


@router.post("", response_model=CredentialSummary, status_code=status.HTTP_201_CREATED)
def create_credential(
    request: CredentialCreateRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    door: RequestDoor,
    services: Services,
) -> CredentialSummary:
    """Create a credential at revision 1 on the forge `forge` names, under `binding`, reading `secret`; its `name` is
    its `slug` when omitted. 422 naming the field for a malformed `slug` or `name`, with the code
    `credential_binding_unknown` for a binding not known, `credential_forge_kind_mismatch` for a forge of another kind
    than the binding's, `credential_settings_invalid` for settings the binding refuses, `forge_unavailable` for an
    unknown or retired forge, and `secret_unavailable` for an unknown or retired secret; 409 for a slug some credential
    holds, or an authority another credential on the forge acts as, naming it."""
    forge = services.forge_records.resolve(request.forge)
    draft = CredentialDraft(
        slug=request.slug,
        name=request.name,
        forge_id=ForgeId(request.forge) if forge is None else forge.forge_id,
        binding=request.binding,
        secret_name=request.secret,
        settings=request.settings,
    )
    try:
        record = services.config_authoring.create_credential(draft, change_context(identity, door))
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except (CredentialSlugTaken, CredentialAuthorityTaken) as exc:
        raise _conflict(exc) from exc
    return _shown(record, services)


@router.get("/{credential}", response_model=CredentialDocument, dependencies=[Depends(require(Permission.FLEET_VIEW))])
def get_credential(credential: PathCredential, services: Services) -> CredentialDocument:
    """One credential by id, current slug or former slug, retired or not, with every repository and work source
    naming it. 404 on an unknown credential."""
    credential_id = credential.credential_id
    repositories = services.repository_records.naming_credential(credential_id)
    sources = services.work_source_records.naming_credential(credential_id)
    return CredentialDocument(
        **_shown(credential, services).model_dump(),
        referrers=[
            *(
                CredentialReferenceView(kind=UseKind.REPOSITORY, key=r.repository_id, label=r.slug)
                for r in repositories
            ),
            *(CredentialReferenceView(kind=UseKind.WORK_SOURCE, key=s.work_source_id, label=s.slug) for s in sources),
        ],
    )


@router.patch("/{credential}", response_model=CredentialSummary)
def patch_credential(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    credential: PathCredential,
    request: CredentialPatchRequest,
    door: RequestDoor,
    services: Services,
    if_match: Annotated[int | None, Header()] = None,
) -> CredentialSummary:
    """Apply only the fields present; a patch that changes nothing writes nothing, and a slug that changes keeps
    resolving to the credential until another claims it. A new `secret` is written to every record acting with the
    credential. 404 unknown; 409 for a stale `If-Match` (naming the current revision), a slug some credential holds,
    or a taken authority; 422 naming the field, coded as on create, `credential_binding_immutable` for a `forge` or
    `binding`, and `forge_capability_missing` for settings that would no longer offer what an enabled repository
    acting with the credential needs, listing each."""
    edit = CredentialEdit(**{_EDIT_FIELDS[field]: getattr(request, field) for field in request.model_fields_set})
    try:
        record = services.config_authoring.edit_credential(
            credential, edit, change_context(identity, door), if_match=if_match
        )
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except (ConfigRevisionConflict, CredentialSlugTaken, CredentialAuthorityTaken) as exc:
        raise _conflict(exc) from exc
    return _shown(record, services)


@router.post("/{credential}/retire", response_model=CredentialSummary)
def retire_credential(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    credential: PathCredential,
    door: RequestDoor,
    services: Services,
    if_match: Annotated[int | None, Header()] = None,
) -> CredentialSummary:
    """Retire a credential only when no enabled repository or work source names it; retiring a retired credential
    changes nothing. 404 unknown; 409 for a stale `If-Match`, or naming each enabled record still naming it."""
    try:
        record = services.config_authoring.retire_credential(
            credential, change_context(identity, door), if_match=if_match
        )
    except (ConfigRevisionConflict, CredentialInUse) as exc:
        raise _conflict(exc) from exc
    return _shown(record, services)


@router.post("/{credential}/enable", response_model=CredentialSummary)
def enable_credential(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    credential: PathCredential,
    door: RequestDoor,
    services: Services,
    if_match: Annotated[int | None, Header()] = None,
) -> CredentialSummary:
    """Enable a retired credential; enabling an enabled one changes nothing. 404 unknown, 409 for a stale `If-Match`,
    422 when its forge or secret has since been retired."""
    try:
        record = services.config_authoring.enable_credential(
            credential, change_context(identity, door), if_match=if_match
        )
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except ConfigRevisionConflict as exc:
        raise _conflict(exc) from exc
    return _shown(record, services)


@router.post("/{credential}/checks", response_model=CredentialCheckView)
def check_credential(
    _identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    credential: PathCredential,
    request: CredentialCheckRequest,
    services: Services,
) -> CredentialCheckView:
    """Check a credential read-only against the repositories and work sources named by id or slug, else against every
    enabled record acting with it: each read landing and ingestion make, and each write judged from the grant the
    credential exposes, never exercised. Stops at 60 seconds, answering the uses not reached, with
    `unreached_reason` when the forge gave no answer to building the credential's authorization; stores nothing but one
    `credential-check` event. 404 unknown; 422 `credential_not_used_by` naming each named record not acting with the
    credential, and `secret_unavailable` for a retired credential or an unknown or retired secret."""
    try:
        check = services.credential_readiness.check(
            credential, repositories=request.repositories, work_sources=request.work_sources
        )
    except CredentialNotUsedBy as exc:
        uses = [RecordReferrer(kind=use.kind.value, key=use.key, label=use.label) for use in exc.uses]
        raise ProblemHTTPException(partial(problems_admin.credential_not_used_by_problem, str(exc), uses)) from exc
    except CredentialUnavailable as exc:
        raise ProblemHTTPException(
            partial(problems_admin.config_field_invalid_problem, "path", "credential", str(exc), "secret_unavailable")
        ) from exc
    return _check_view(check)


def _check_view(check: CredentialCheck) -> CredentialCheckView:
    return CredentialCheckView(
        credential=check.credential,
        binding=check.binding,
        variant=check.variant,
        variant_contradicted=check.variant_contradicted,
        mint=_operation_view(check.mint) if check.mint is not None else None,
        granted_permissions=dict(check.granted_permissions) if check.granted_permissions is not None else None,
        unreached_reason=check.unreached_reason,
        uses=[
            UseResultView(
                kind=use.kind,
                id=use.id,
                slug=use.slug,
                operations=[_operation_view(operation) for operation in use.operations],
                reached=use.reached,
            )
            for use in check.uses
        ],
        not_evaluated=check.not_evaluated,
    )


def _operation_view(operation: OperationResult) -> OperationResultView:
    return OperationResultView(
        operation=operation.operation,
        outcome=operation.outcome,
        detail=operation.detail,
        diagnostic=_diagnostic_view(operation.diagnostic) if operation.diagnostic is not None else None,
    )


def _diagnostic_view(diagnostic: ForgeDiagnostic) -> ForgeDiagnosticView:
    return ForgeDiagnosticView(
        status=diagnostic.status,
        operation=diagnostic.operation,
        endpoint=diagnostic.endpoint,
        repository=diagnostic.repository,
        message=diagnostic.message,
        documentation_url=diagnostic.documentation_url,
        request_id=diagnostic.request_id,
        accepted_permissions=diagnostic.accepted_permissions,
        credential=diagnostic.credential,
        binding=diagnostic.binding,
        variant=diagnostic.variant,
    )
