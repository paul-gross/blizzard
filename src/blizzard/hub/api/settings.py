"""The tenant's settings routes — show and edit, keyed by nothing: the tenant the request resolved is the key.

An edit is a sparse merge with ``If-Match`` on the settings' ``revision``. Changing the default moves no chunk; it
decides only what is minted from then on."""

from __future__ import annotations

from functools import partial
from typing import Annotated

from fastapi import APIRouter, Depends, Header

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import problems, problems_admin
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.deps import Services
from blizzard.hub.api.door import RequestDoor, change_context
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.auth.permissions import Permission
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.kernel.unset import UNSET
from blizzard.hub.domain.urgency.services import SettingsView as SettingsRead
from blizzard.hub.domain.urgency.settings import DEFAULT_URGENCY_FIELD, TenantSettings
from blizzard.wire.urgency import SettingsPatchRequest, SettingsView, UrgencyRef

router = APIRouter(
    prefix="/api/settings",
    tags=["settings"],
    dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)],
)


def _view(read: SettingsRead) -> SettingsView:
    return SettingsView(
        default_urgency=UrgencyRef(urgency_id=read.default.urgency_id, slug=read.default.slug),
        revision=read.settings.revision,
        updated_at=iso_utc(read.settings.updated_at),
        updated_by=read.settings.updated_by,
    )


@router.get("", response_model=SettingsView, dependencies=[Depends(require(Permission.FLEET_VIEW))])
def get_settings(services: Services) -> SettingsView:
    """The tenant's settings."""
    return _view(services.tenant_settings.show())


@router.patch("", response_model=SettingsView)
def patch_settings(
    request: SettingsPatchRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    door: RequestDoor,
    services: Services,
    if_match: Annotated[int | None, Header()] = None,
) -> SettingsView:
    """Edit the stated settings, keeping the revision on a no-op. 422 `urgency_unknown` for a `default_urgency`
    naming no urgency of the tenant, `urgency_retired` for a retired one, and 422 for `null`; 409 on a stale
    `If-Match`."""
    sent = request.model_fields_set
    try:
        default = (
            services.urgency_reading.named(DEFAULT_URGENCY_FIELD, TenantSettings.named_default(request.default_urgency))
            if "default_urgency" in sent
            else UNSET
        )
        edited = services.tenant_settings.edit(
            change_context(identity, door), default_urgency=default, if_match=if_match
        )
    except ConfigFieldError as exc:
        raise ProblemHTTPException(
            partial(problems_admin.config_field_invalid_problem, "body", exc.field, exc.message, exc.code)
        ) from exc
    except ConfigRevisionConflict as exc:
        raise ProblemHTTPException(partial(problems.revision_conflict_problem, str(exc))) from exc
    return _view(edited)
