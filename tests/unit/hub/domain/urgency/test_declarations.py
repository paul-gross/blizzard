"""The urgency sections of a configuration document planned as pure values: the creates, edits, order and default an
apply writes, the refusals it meets, and the keyed locks it names."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.foundation.project_colors import ProjectColor
from blizzard.hub.domain.config.apply import ApplyEntryRefused, ApplyPlan
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, Door, RecordKind
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.urgency.declarations import (
    NamedRevision,
    SettingsWrite,
    StoredUrgencies,
    UrgencyDeclaration,
    UrgencyOrderWrite,
    UrgencySections,
    UrgencyWrite,
    plan_urgencies,
)
from blizzard.hub.domain.urgency.model import Urgency, UrgencyRetired, UrgencyUnknown
from blizzard.hub.domain.urgency.order import Placed, UrgencyOrder, UrgencyOrderIncomplete
from blizzard.hub.domain.urgency.ports import tenant_settings_lock, urgency_lock, urgency_order_lock
from blizzard.hub.domain.urgency.settings import TenantSettings

_AT = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
_CTX = ChangeContext(actor="usr_op", door=Door.APPLY, apply_id="cfa_1")
_TENANT = "ten_1"
_SEQ = 7


def _stored(*, retired: tuple[str, ...] = ()) -> StoredUrgencies:
    """``standard`` (the default) and ``urgent``, ``urgent`` first, plus each of ``retired`` after them, retired."""
    standard, _ = Urgency.standard(_CTX, at=_AT)
    urgent = replace(standard, urgency_id="urg_urgent", slug="urgent", name="urgent", color=ProjectColor.RED)
    gone = [
        replace(standard, urgency_id=f"urg_{slug}", slug=slug, name=slug, revision=2, retired=True) for slug in retired
    ]
    held = (standard, urgent, *gone)
    placed = [
        Placed(urgent.urgency_id, urgent.slug, 0.0),
        Placed(standard.urgency_id, standard.slug, 1.0),
        *(Placed(u.urgency_id, u.slug, 2.0 + i, retired=True) for i, u in enumerate(gone)),
    ]
    settings = TenantSettings(_TENANT, standard.urgency_id, 3, _AT, "usr_op")
    return StoredUrgencies(_TENANT, held, UrgencyOrder.of(placed, _SEQ), settings)


def _plan(declared: UrgencySections, stored: StoredUrgencies | None = None) -> ApplyPlan:
    return plan_urgencies(declared, stored or _stored(), _CTX, pick=lambda colors: colors[0], at=_AT)


def _refusal(declared: UrgencySections, stored: StoredUrgencies | None = None) -> ApplyEntryRefused:
    with pytest.raises(ApplyEntryRefused) as refused:
        _plan(declared, stored)
    return refused.value


def _rows(plan: ApplyPlan) -> list[tuple[str, str, str | None]]:
    return [(o.kind.value, o.key, None if o.op is None else o.op.value) for o in plan.outcomes]


def test_a_document_stating_the_stored_set_plans_no_write_and_takes_no_lock() -> None:
    plan = _plan(
        UrgencySections(
            urgencies=(UrgencyDeclaration("urgent", color="red"), UrgencyDeclaration("standard")),
            order=("urgent", "standard"),
            default_urgency="standard",
        )
    )

    assert plan.writes == ()
    assert plan.locks == ()
    assert _rows(plan) == [
        ("urgency", "urgent", None),
        ("urgency", "standard", None),
        ("urgency_order", "urgency_order", None),
        ("tenant_settings", "settings", None),
    ]


def test_new_urgencies_land_at_the_least_urgent_end_in_document_order_on_the_sequence_read() -> None:
    plan = _plan(UrgencySections(urgencies=(UrgencyDeclaration("later"), UrgencyDeclaration("never"))))

    records = [w.record for w in plan.writes]
    assert all(isinstance(r, UrgencyWrite) for r in records)
    assert [(r.urgency.slug, r.position, r.order_seq) for r in records if isinstance(r, UrgencyWrite)] == [
        ("later", 2.0, _SEQ),
        ("never", 3.0, _SEQ),
    ]
    assert [w.from_revision for w in plan.writes] == [None, None]
    assert plan.locks == tuple(sorted([tenant_settings_lock(), urgency_order_lock()]))


def test_an_entry_edits_only_the_fields_it_states_on_the_revision_read() -> None:
    plan = _plan(UrgencySections(urgencies=(UrgencyDeclaration("urgent", name="Urgent", emoji="🔥"),)))

    (write,) = plan.writes
    assert isinstance(write.record, UrgencyWrite)
    assert (write.record.urgency.name, write.record.urgency.emoji, write.record.urgency.color) == (
        "Urgent",
        "🔥",
        ProjectColor.RED,
    )
    assert write.from_revision == 1
    assert write.change.op is ChangeOp.EDIT
    assert plan.locks == (tenant_settings_lock(),)


def test_one_document_declares_an_urgency_orders_it_first_and_makes_it_the_default() -> None:
    plan = _plan(
        UrgencySections(
            urgencies=(UrgencyDeclaration("now"),),
            order=("now", "urgent", "standard"),
            default_urgency="now",
        )
    )

    create, order, settings = (w.record for w in plan.writes)
    assert isinstance(create, UrgencyWrite) and isinstance(order, UrgencyOrderWrite)
    assert isinstance(settings, SettingsWrite)
    new_id = create.urgency.urgency_id
    assert order.from_seq == _SEQ
    assert [named.slug for named in order.named] == ["now", "urgent", "standard"]
    assert settings.settings.default_urgency_id == new_id
    assert settings.default == NamedRevision(new_id, 1, "now")
    assert [w.from_revision for w in plan.writes] == [None, _SEQ, 3]
    assert plan.outcomes[1].diff[0].old == ["urgent", "standard", "now"]
    assert plan.outcomes[1].diff[0].new == ["now", "urgent", "standard"]
    assert plan.outcomes[2].kind is RecordKind.TENANT_SETTINGS
    assert plan.locks == tuple(sorted([tenant_settings_lock(), urgency_order_lock(), urgency_lock(new_id)]))


def test_an_entry_naming_a_retired_urgency_is_refused_rather_than_enabled() -> None:
    refused = _refusal(UrgencySections(urgencies=(UrgencyDeclaration("gone"),)), _stored(retired=("gone",)))

    assert (refused.section, refused.index) == ("urgencies", 0)
    assert isinstance(refused.cause, UrgencyRetired)


@pytest.mark.parametrize(
    ("declared", "section", "cause"),
    [
        (UrgencySections(order=("urgent", "standard", "gone")), "urgency_order", UrgencyRetired),
        (UrgencySections(default_urgency="gone"), "settings", UrgencyRetired),
        (UrgencySections(order=("urgent", "standard", "nope")), "urgency_order", UrgencyUnknown),
        (UrgencySections(default_urgency="nope"), "settings", UrgencyUnknown),
    ],
)
def test_a_reference_to_a_retired_or_unknown_urgency_is_refused(
    declared: UrgencySections, section: str, cause: type[Exception]
) -> None:
    refused = _refusal(declared, _stored(retired=("gone",)))

    assert refused.section == section
    assert isinstance(refused.cause, cause)


def test_an_order_leaving_out_a_live_urgency_is_incomplete_counting_the_ones_the_document_creates() -> None:
    refused = _refusal(UrgencySections(urgencies=(UrgencyDeclaration("later"),), order=("urgent", "standard")))

    assert refused.section == "urgency_order"
    assert isinstance(refused.cause, UrgencyOrderIncomplete)
    assert "later" in str(refused.cause)


def test_an_order_naming_an_urgency_twice_is_incomplete_at_the_repeat() -> None:
    refused = _refusal(UrgencySections(order=("urgent", "standard", "urgent")))

    assert (refused.section, refused.index) == ("urgency_order", 2)
    assert isinstance(refused.cause, UrgencyOrderIncomplete)


def test_a_null_default_is_refused() -> None:
    refused = _refusal(UrgencySections(default_urgency=None))

    assert refused.section == "settings"
    assert isinstance(refused.cause, ConfigFieldError)
    assert refused.cause.field == "default_urgency"


def test_an_urgency_named_twice_is_refused_at_the_repeat() -> None:
    refused = _refusal(UrgencySections(urgencies=(UrgencyDeclaration("later"), UrgencyDeclaration("later"))))

    assert (refused.section, refused.index) == ("urgencies", 1)
