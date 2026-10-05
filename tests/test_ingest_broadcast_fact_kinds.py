"""Every runner fact kind the kernel declares is classified for the ingest broadcast exactly once —
runner-scoped (published by its own arm) or chunk-scoped (routed by its payload's ``chunk_id``)."""

from __future__ import annotations

import pytest

from blizzard.foundation import fact_kinds
from blizzard.hub.api.ingest_broadcast import _CHUNK_SCOPED_FACT_KINDS, _RUNNER_ARM_BY_FACT_KIND, IngestBroadcast
from blizzard.wire.facts import RunnerFact

pytestmark = pytest.mark.unit


def _declared_fact_kinds() -> set[str]:
    return {
        value
        for name, value in vars(fact_kinds).items()
        if name.isupper() and isinstance(value, str) and "." in value and not name.startswith("_")
    }


def test_every_declared_fact_kind_is_runner_scoped_or_chunk_scoped_never_both() -> None:
    runner_scoped = set(_RUNNER_ARM_BY_FACT_KIND)

    assert runner_scoped & _CHUNK_SCOPED_FACT_KINDS == set()
    assert runner_scoped | _CHUNK_SCOPED_FACT_KINDS == _declared_fact_kinds()


@pytest.mark.parametrize("kind", sorted(_RUNNER_ARM_BY_FACT_KIND))
def test_a_runner_scoped_kind_never_reaches_the_chunk_arm(kind: str) -> None:
    fact = RunnerFact(seq=1, kind=kind, payload={"chunk_id": "ch_1"})

    assert IngestBroadcast._chunk_arm_id(fact) is None


@pytest.mark.parametrize("kind", sorted(_CHUNK_SCOPED_FACT_KINDS))
def test_a_chunk_scoped_kind_reaches_the_chunk_arm_on_its_chunk_id(kind: str) -> None:
    assert IngestBroadcast._chunk_arm_id(RunnerFact(seq=1, kind=kind, payload={"chunk_id": "ch_1"})) == "ch_1"
    assert IngestBroadcast._chunk_arm_id(RunnerFact(seq=1, kind=kind, payload={})) is None
