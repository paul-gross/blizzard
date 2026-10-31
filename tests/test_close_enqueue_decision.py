import pytest

from blizzard.hub.domain.chunk.model import CloseEnqueueDecision, WorkRef, decide_close_enqueue

pytestmark = pytest.mark.unit


def test_close_enqueue_partitions_refs_by_source() -> None:
    first = WorkRef("supported", "1")
    second = WorkRef("supported", "2")
    missing = WorkRef("missing", "3")

    assert decide_close_enqueue([first, second, missing, first], lambda source: source == "supported") == (
        CloseEnqueueDecision(closable=frozenset({first, second}), skipped=frozenset({missing}))
    )


def test_close_enqueue_empty_refs_does_not_query_sources() -> None:
    def unavailable(source: str) -> bool:
        raise AssertionError(f"unexpected lookup: {source}")

    assert decide_close_enqueue([], unavailable) == CloseEnqueueDecision(closable=frozenset(), skipped=frozenset())
