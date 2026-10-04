"""A secret's lifecycle decisions: which change row each verb writes, or why it is refused.

Which verbs are legal from which state is :data:`SECRET_TRANSITIONS`. The secret's retired
state is a plain value here; the caller reads it from the catalog. A retire or enable writes its
change row at the secret's unchanged revision: the value is sealed against ``(name, revision)``,
so moving the revision without resealing would leave the value undecryptable."""

from __future__ import annotations

from datetime import datetime

from blizzard.hub.domain.config.changes import (
    RETIRED_FIELD,
    SECRET_TRANSITIONS,
    ChangeContext,
    ChangeOp,
    ConfigChange,
    FieldChange,
    RecordKind,
    RecordState,
    Verdict,
)
from blizzard.hub.domain.config.secrets import SecretMetadata, SecretName, SecretRetired, SecretRevisionConflict


def creation(name: SecretName, ctx: ChangeContext, *, at: datetime) -> ConfigChange:
    """The change row a new secret writes at revision 1 — no diff, since a value is never logged."""
    return ConfigChange.of(ctx, RecordKind.SECRET, name.value, 1, ChangeOp.CREATE, (), at)


def replacement(
    record: SecretMetadata, ctx: ChangeContext, *, retired: bool, if_match: int | None, at: datetime
) -> ConfigChange:
    """The change row a value replacement writes at the next revision. A retired secret is
    refused before a stale ``if_match`` is; a race lost after this is the store's compare-and-set."""
    if SECRET_TRANSITIONS[RecordState.of(retired)][ChangeOp.REPLACE] is Verdict.REFUSED:
        raise SecretRetired(record.name)
    if if_match is not None and if_match != record.revision:
        raise SecretRevisionConflict(record.name, current=record.revision)
    return ConfigChange.of(ctx, RecordKind.SECRET, record.name, record.revision + 1, ChangeOp.REPLACE, (), at)


def lifecycle_change(
    record: SecretMetadata, ctx: ChangeContext, *, retired_now: bool, retired: bool, at: datetime
) -> ConfigChange | None:
    """The change row a retire (``retired``) or enable writes; ``None`` when the secret already
    stands there and nothing is written."""
    op = ChangeOp.RETIRE if retired else ChangeOp.ENABLE
    if SECRET_TRANSITIONS[RecordState.of(retired_now)][op] is Verdict.NO_OP:
        return None
    flip = (FieldChange(RETIRED_FIELD, retired_now, retired),)
    return ConfigChange.of(ctx, RecordKind.SECRET, record.name, record.revision, op, flip, at)
