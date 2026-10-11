"""A hub node lands a chunk only in repositories its project links: the refusal each resolution outcome
records, and the record names a visit holds for the markers it lands."""

from __future__ import annotations

import pytest

from blizzard.foundation.event_log import EVENT_LOG_SEVERITY, EventLogSeverity
from blizzard.hub.delivery.hub_node import REFUSAL_EVENT
from blizzard.hub.delivery.marker_auth import MarkerAuthority
from blizzard.hub.domain.config.repositories import RepositoryOutcome
from blizzard.hub.tenancy import TenantId

pytestmark = pytest.mark.unit

_REFUSED = (RepositoryOutcome.UNRESOLVED, RepositoryOutcome.NOT_LINKED, RepositoryOutcome.DISAGREE)


@pytest.mark.parametrize("outcome", list(RepositoryOutcome))
def test_every_resolution_outcome_maps_to_the_event_it_records(outcome: RepositoryOutcome) -> None:
    expected = outcome.value if outcome in _REFUSED else None

    assert REFUSAL_EVENT[outcome] == expected


@pytest.mark.parametrize("outcome", _REFUSED)
def test_every_refused_outcome_records_a_critical_event(outcome: RepositoryOutcome) -> None:
    kind = REFUSAL_EVENT[outcome]

    assert kind is not None
    assert EVENT_LOG_SEVERITY[kind] is EventLogSeverity.CRITICAL


def _held(authority: MarkerAuthority) -> MarkerAuthority:
    authority.issue("ch_1", node_id="n_deliver", epoch=2, tenant=TenantId("t_1"))
    authority.hold_repositories("ch_1", node_id="n_deliver", epoch=2, names={"acme/widget": "widget"})
    return authority


def test_a_visit_answers_the_record_name_it_holds_for_a_repository() -> None:
    authority = _held(MarkerAuthority())

    assert authority.repository_name("ch_1", node_id="n_deliver", epoch=2, repo="acme/widget") == "widget"
    assert authority.repository_name("ch_1", node_id="n_deliver", epoch=2, repo="acme/gadget") is None


def test_another_visit_of_the_chunk_holds_none_of_its_names() -> None:
    authority = _held(MarkerAuthority())

    assert authority.repository_name("ch_1", node_id="n_deliver", epoch=3, repo="acme/widget") is None
    assert authority.repository_name("ch_1", node_id="n_other", epoch=2, repo="acme/widget") is None


def test_revoking_a_visit_drops_the_names_it_held_with_its_token() -> None:
    authority = _held(MarkerAuthority())

    authority.revoke("ch_1", node_id="n_deliver", epoch=2)

    assert authority.repository_name("ch_1", node_id="n_deliver", epoch=2, repo="acme/widget") is None
