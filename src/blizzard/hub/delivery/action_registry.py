"""The hub's actions: the catalogue graph validation reads and the implementation the executor takes a
pass with, keyed by one name.

The two test actions join only when ``BLIZZARD_TEST_ACTIONS=1`` is set beside the crash fence
``BLIZZARD_CRASH_FENCE=1``, read once at import as ``src/blizzard/foundation/crash.py`` reads its fence, so an
installation can never mint them. The read lives here, outside ``hub/delivery/actions/``, whose modules
may not import ``os`` (``bzh:hub-action-purity``)."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from blizzard.foundation.crash import ENV_CRASH_FENCE
from blizzard.foundation.roles import collaborator
from blizzard.hub.delivery.actions.base import IHubAction
from blizzard.hub.delivery.actions.garden_deliver import GardenDeliver
from blizzard.hub.delivery.actions.land import Land
from blizzard.hub.delivery.actions.pass_ import PassAction
from blizzard.hub.delivery.actions.record_findings import RecordFindings
from blizzard.hub.delivery.actions.test_actions import AnswerAction, SequenceAction
from blizzard.hub.domain.graph.actions import HubActionCatalogue

#: Admits the test actions, beside the crash fence.
ENV_TEST_ACTIONS = "BLIZZARD_TEST_ACTIONS"


@collaborator
@dataclass(frozen=True)
class HubActions:
    """The catalogue and the implementations behind it, naming the same set."""

    catalogue: HubActionCatalogue
    implementations: Mapping[str, IHubAction]

    @classmethod
    def of(cls, actions: Iterable[IHubAction]) -> HubActions:
        built = list(actions)
        return cls(
            catalogue=HubActionCatalogue.of(action.spec for action in built),
            implementations=MappingProxyType({action.spec.name: action for action in built}),
        )

    def action(self, name: str) -> IHubAction | None:
        return self.implementations.get(name)


SHIPPED_ACTIONS = HubActions.of([Land(), RecordFindings(), GardenDeliver(), PassAction()])
TEST_ACTIONS: tuple[IHubAction, ...] = (AnswerAction(), SequenceAction())


def _test_actions_admitted() -> bool:
    return os.environ.get(ENV_TEST_ACTIONS) == "1" and os.environ.get(ENV_CRASH_FENCE) == "1"


#: What this hub process serves: the shipped actions, and the test actions behind both fences.
HUB_ACTIONS = (
    HubActions.of([*SHIPPED_ACTIONS.implementations.values(), *TEST_ACTIONS])
    if _test_actions_admitted()
    else SHIPPED_ACTIONS
)
