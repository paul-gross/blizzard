"""The tenant's settings: one singleton configured record per tenant, keyed by the tenant's id, naming the urgency every
new chunk takes when nothing names another.

The settings take show and edit only: a tenant always has its settings, seeded with the tenant, so they are neither
created by an operator nor retired. Every write moves the ``revision`` and appends a ``tenant_settings`` change row,
which names no project and lists the default by slug."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config import lifecycle
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, ConfigChange, FieldChange, RecordKind
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.urgency.model import Urgency, UrgencyId

#: The field the default is written under on the wire and in a diff.
DEFAULT_URGENCY_FIELD = "default_urgency"


@domain_model
@dataclass(frozen=True)
class TenantSettings:
    """A tenant's settings as stored."""

    tenant_id: str
    default_urgency_id: UrgencyId
    revision: int
    updated_at: datetime
    updated_by: str

    @classmethod
    def seed(
        cls, tenant_id: str, default: Urgency, ctx: ChangeContext, *, at: datetime
    ) -> tuple[TenantSettings, ConfigChange]:
        """A new tenant's settings at revision 1, naming ``default``, with their ``create`` change."""
        record = cls(tenant_id, default.urgency_id, 1, at, ctx.actor)
        diff = (FieldChange(DEFAULT_URGENCY_FIELD, None, default.slug),)
        return record, record._change(ctx, ChangeOp.CREATE, diff, at)

    @staticmethod
    def named_default(reference: str | None) -> str:
        """``reference`` — the urgency an edit names as the default — when it names one: the default is never
        cleared, so a ``null`` is refused."""
        if reference is None:
            raise ConfigFieldError(DEFAULT_URGENCY_FIELD, "must not be null")
        return reference

    def require_revision(self, if_match: int | None) -> None:
        """:class:`ConfigRevisionConflict` when ``if_match`` names a revision other than the stored one."""
        lifecycle.require_revision(if_match, self.revision, kind="tenant_settings", key=self.tenant_id)

    def point_at(
        self, default: Urgency, *, held: Urgency, ctx: ChangeContext, if_match: int | None, at: datetime
    ) -> tuple[TenantSettings, ConfigChange] | None:
        """The settings naming ``default`` — a live urgency the caller confirmed under its lock — in place of ``held``,
        the urgency they name now; ``None`` when they already name it. The revision check comes first."""
        self.require_revision(if_match)
        if default.urgency_id == self.default_urgency_id:
            return None
        edited = replace(
            self, default_urgency_id=default.urgency_id, revision=self.revision + 1, updated_at=at, updated_by=ctx.actor
        )
        diff = (FieldChange(DEFAULT_URGENCY_FIELD, held.slug, default.slug),)
        return edited, edited._change(ctx, ChangeOp.EDIT, diff, at)

    def _change(self, ctx: ChangeContext, op: ChangeOp, diff: tuple[FieldChange, ...], at: datetime) -> ConfigChange:
        return ConfigChange.of(ctx, RecordKind.TENANT_SETTINGS, self.tenant_id, self.revision, op, diff, at)
