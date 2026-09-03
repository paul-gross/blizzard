"""``derive_liveness`` — the newest-fact-wins finding read (unit tier, blizzard#390):
no facts reads live with nothing seen; a plain add/observe history stays live; a `gone`
takes it out of the live bucket; a later fact after `gone` restores it (D3); and
`observed_count` counts only `observed` facts, never the initial `add`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.findings import FindingFact, derive_liveness

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_T1 = datetime(2026, 1, 8, tzinfo=UTC)
_T2 = datetime(2026, 1, 15, tzinfo=UTC)


def test_no_facts_reads_live_with_nothing_seen() -> None:
    state = derive_liveness([])

    assert state.live is True
    assert state.first_observed_at is None
    assert state.last_seen_at is None
    assert state.observed_count == 0


def test_first_observed_at_is_the_add_facts_own_instant() -> None:
    facts = [FindingFact(kind="add", recorded_at=_T0), FindingFact(kind="observed", recorded_at=_T2)]

    state = derive_liveness(facts)

    assert state.first_observed_at == _T0
    assert state.last_seen_at == _T2


def test_first_observed_at_reads_off_recorded_at_not_insertion_order() -> None:
    """The same out-of-order guarantee `last_seen_at` carries: both ends of the
    add/observed span come from `recorded_at`, so ingesting facts out of order still
    derives the true first and last instants."""
    facts = [FindingFact(kind="observed", recorded_at=_T2), FindingFact(kind="add", recorded_at=_T0)]

    state = derive_liveness(facts)

    assert state.first_observed_at == _T0
    assert state.last_seen_at == _T2


def test_an_exit_verb_does_not_move_first_observed_at() -> None:
    """An exit is not an observation — only `add`/`observed` bound the span."""
    facts = [
        FindingFact(kind="add", recorded_at=_T1),
        FindingFact(kind="resolved", recorded_at=_T2, note="fixed", actor="usr_1"),
    ]

    state = derive_liveness(facts)

    assert state.first_observed_at == _T1


def test_a_freshly_added_finding_is_live() -> None:
    state = derive_liveness([FindingFact(kind="add", recorded_at=_T0)])

    assert state.live is True
    assert state.last_seen_at == _T0
    assert state.observed_count == 0


def test_an_observed_finding_updates_last_seen_and_the_observed_count() -> None:
    facts = [FindingFact(kind="add", recorded_at=_T0), FindingFact(kind="observed", recorded_at=_T1)]

    state = derive_liveness(facts)

    assert state.live is True
    assert state.last_seen_at == _T1
    assert state.observed_count == 1


def test_a_gone_fact_takes_it_out_of_the_live_bucket() -> None:
    facts = [
        FindingFact(kind="add", recorded_at=_T0),
        FindingFact(kind="gone", recorded_at=_T1, note="no longer reproduces"),
    ]

    state = derive_liveness(facts)

    assert state.live is False
    assert state.last_seen_at == _T0  # last seen is the newest non-gone fact
    assert state.observed_count == 0


def test_a_later_fact_after_gone_restores_liveness() -> None:
    """`gone` does not close the finding
    (blizzard-context:/domain/findings-and-proposals.md §Liveness is derived, and
    reversible) — a later `observed` restores it."""
    facts = [
        FindingFact(kind="add", recorded_at=_T0),
        FindingFact(kind="gone", recorded_at=_T1, note="not found this run"),
        FindingFact(kind="observed", recorded_at=_T2),
    ]

    state = derive_liveness(facts)

    assert state.live is True
    assert state.last_seen_at == _T2
    assert state.observed_count == 1
