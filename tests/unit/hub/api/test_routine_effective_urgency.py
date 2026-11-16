"""A routine view's effective urgency resolves through the chain: the routine's live default, else the tenant's —
including a retired routine link, which the retire verb clears and so only a stored record can carry."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from blizzard.foundation.project_colors import ProjectColor
from blizzard.hub.api.routines import effective_urgency
from blizzard.hub.domain.garden.routines import Routine
from blizzard.hub.domain.urgency.model import Urgency, UrgencyId
from blizzard.wire.urgency import UrgencyRef

_T0 = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _urgency(slug: str, *, retired: bool = False) -> Urgency:
    return Urgency(
        urgency_id=UrgencyId(f"urg_{slug}"),
        slug=slug,
        name=slug.title(),
        color=ProjectColor.SLATE,
        emoji=None,
        icon=None,
        created_at=_T0,
        created_by="operator",
        retired=retired,
    )


_STANDARD = _urgency("standard")
_OLD = _urgency("old", retired=True)
_URGENT = _urgency("urgent")
_URGENCIES = {u.urgency_id: u for u in (_STANDARD, _OLD, _URGENT)}
_ROUTINE = Routine(
    routine_id="rtn_1",
    slug="gardening",
    name="gardening",
    graph_name="default",
    default_scope_slug="blizzard",
    created_at=_T0,
    project_id="prj_1",
)


def test_a_retired_routine_link_falls_through_to_the_tenant_default() -> None:
    routine = replace(_ROUTINE, default_urgency_id=_OLD.urgency_id)

    assert effective_urgency(routine, _URGENCIES, _STANDARD.urgency_id) == UrgencyRef(
        urgency_id=_STANDARD.urgency_id, slug="standard"
    )


def test_a_live_routine_link_outranks_the_tenant_default() -> None:
    routine = replace(_ROUTINE, default_urgency_id=_URGENT.urgency_id)

    assert effective_urgency(routine, _URGENCIES, _STANDARD.urgency_id) == UrgencyRef(
        urgency_id=_URGENT.urgency_id, slug="urgent"
    )


def test_an_unresolvable_tenant_default_names_none_rather_than_failing_the_read() -> None:
    assert effective_urgency(_ROUTINE, _URGENCIES, _OLD.urgency_id) is None
