"""A hub node lands a chunk only in repositories its project links: the refusal each resolution outcome
records."""

from __future__ import annotations

import pytest

from blizzard.foundation.event_log import EVENT_LOG_SEVERITY, EventLogSeverity
from blizzard.hub.delivery.hub_node import REFUSAL_EVENT
from blizzard.hub.domain.config.repositories import RepositoryOutcome

pytestmark = pytest.mark.unit

_REFUSED = (RepositoryOutcome.UNRESOLVED, RepositoryOutcome.NOT_LINKED)


@pytest.mark.parametrize("outcome", list(RepositoryOutcome))
def test_every_resolution_outcome_maps_to_the_event_it_records(outcome: RepositoryOutcome) -> None:
    expected = outcome.value if outcome in _REFUSED else None

    assert REFUSAL_EVENT[outcome] == expected


@pytest.mark.parametrize("outcome", _REFUSED)
def test_every_refused_outcome_records_a_critical_event(outcome: RepositoryOutcome) -> None:
    kind = REFUSAL_EVENT[outcome]

    assert kind is not None
    assert EVENT_LOG_SEVERITY[kind] is EventLogSeverity.CRITICAL
