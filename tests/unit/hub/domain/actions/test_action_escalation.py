"""The typed missing-capability reason and its pairing with the escalation cause."""

import pytest

from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.hub.domain.actions.effects import ActionEscalation, MissingCapability
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.repositories import RepositoryId

_REPOSITORY = RepositoryId("re_widget")


def test_the_reason_orders_and_deduplicates_by_capability_value():
    reason = MissingCapability.of(
        _REPOSITORY, [Capability.REFS_DELETE, Capability.BRANCHES_MERGE, Capability.REFS_DELETE]
    )
    assert reason.capabilities == (Capability.BRANCHES_MERGE, Capability.REFS_DELETE)
    assert reason.first is Capability.BRANCHES_MERGE


def test_the_reason_names_at_least_one_capability():
    with pytest.raises(ValueError):
        MissingCapability(_REPOSITORY, ())
    with pytest.raises(ValueError):
        MissingCapability(_REPOSITORY, (Capability.REFS_DELETE, Capability.BRANCHES_MERGE))


def test_the_reason_goes_with_the_capability_cause_and_only_it():
    reason = MissingCapability.of(_REPOSITORY, [Capability.REFS_CREATE])
    assert ActionEscalation(EscalationCause.FORGE_CAPABILITY_MISSING, "needs it", reason).missing is reason
    with pytest.raises(ValueError):
        ActionEscalation(EscalationCause.FORGE_CAPABILITY_MISSING, "needs it")
    with pytest.raises(ValueError):
        ActionEscalation(EscalationCause.FORGE_AUTH, "revoked", reason)
