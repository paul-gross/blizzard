"""The hub action catalogue — every action a hub node may name, described without its
implementation.

It lives in ``graph`` (``bzh:domain-package-layers``) and is shared; it is a value handed
in, never a global read, so a test can hand its own."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from blizzard.foundation.roles import domain_model

# Answers that end a pass without moving the chunk: never authored, never routed, never a
# verdict. A later reserved wait joins this set rather than adding a second rule.
RESERVED_WAITS: frozenset[str] = frozenset({"pending"})


@domain_model
@dataclass(frozen=True)
class ActionSpec:
    """One action as graph validation and the executor see it."""

    name: str
    # The keys ``with:`` may carry, and the ones it must.
    args: frozenset[str]
    required_args: frozenset[str]
    # Every choice a pass may answer, reserved waits included.
    answers: frozenset[str]
    # Set only by a test action whose answers are its argument's values.
    answers_from_arg: str | None
    # Whether a visit is handed forge handles and holds its branches.
    lands: bool

    def answers_for(self, action_args: Mapping[str, tuple[str, ...]]) -> frozenset[str]:
        """The choices a node passing ``action_args`` may be answered with."""
        if self.answers_from_arg is None:
            return self.answers
        return frozenset(action_args.get(self.answers_from_arg, ()))

    def can_wait_for(self, action_args: Mapping[str, tuple[str, ...]]) -> bool:
        """Whether a node passing ``action_args`` can be answered a reserved wait — so it
        may carry poll keys."""
        return bool(self.answers_for(action_args) & RESERVED_WAITS)


@domain_model
@dataclass(frozen=True)
class HubActionCatalogue:
    """The actions a hub knows, keyed by name."""

    specs: Mapping[str, ActionSpec]

    @classmethod
    def of(cls, specs: Iterable[ActionSpec]) -> HubActionCatalogue:
        return cls(MappingProxyType({spec.name: spec for spec in specs}))

    def get(self, name: str) -> ActionSpec | None:
        return self.specs.get(name)

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self.specs)

    def including(self, specs: Iterable[ActionSpec]) -> HubActionCatalogue:
        """This catalogue with ``specs`` added beside its own."""
        return HubActionCatalogue.of([*self.specs.values(), *specs])


LAND = ActionSpec(
    name="land",
    args=frozenset(),
    required_args=frozenset(),
    answers=frozenset({"landed", "conflict", "failure", "inherited-failure", "pending"}),
    answers_from_arg=None,
    lands=True,
)
RECORD_FINDINGS = ActionSpec(
    name="record-findings",
    args=frozenset(),
    required_args=frozenset(),
    answers=frozenset({"recorded", "invalid", "failure"}),
    answers_from_arg=None,
    lands=False,
)
GARDEN_DELIVER = ActionSpec(
    name="garden-deliver",
    args=frozenset({"delta", "proposals"}),
    required_args=frozenset(),
    answers=frozenset({"recorded", "invalid", "invalid-proposals", "failure"}),
    answers_from_arg=None,
    lands=False,
)
PASS = ActionSpec(
    name="pass",
    args=frozenset(),
    required_args=frozenset(),
    answers=frozenset({"success"}),
    answers_from_arg=None,
    lands=False,
)

SHIPPED_CATALOGUE = HubActionCatalogue.of([LAND, RECORD_FINDINGS, GARDEN_DELIVER, PASS])

# The actions a hub process driven by the end-to-end suites and the crash sweep adds,
# behind both test fences; an installation never mints them.
TEST_ANSWER = ActionSpec(
    name="test.answer",
    args=frozenset({"choice"}),
    required_args=frozenset({"choice"}),
    answers=frozenset(),
    answers_from_arg="choice",
    lands=False,
)
TEST_SEQUENCE = ActionSpec(
    name="test.sequence",
    args=frozenset({"choices"}),
    required_args=frozenset({"choices"}),
    answers=frozenset(),
    answers_from_arg="choices",
    lands=False,
)
TEST_BLOCK = ActionSpec(
    name="test.block",
    args=frozenset({"seconds"}),
    required_args=frozenset({"seconds"}),
    answers=frozenset({"success"}),
    answers_from_arg=None,
    lands=False,
)
TEST_ACTION_SPECS: tuple[ActionSpec, ...] = (TEST_ANSWER, TEST_SEQUENCE, TEST_BLOCK)
