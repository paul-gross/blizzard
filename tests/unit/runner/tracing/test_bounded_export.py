from __future__ import annotations

import threading
from collections.abc import Sequence

import pytest

from blizzard.foundation.trace_spans import FinishedSpan
from blizzard.runner.tracing.bounded_export import BoundedTraceExporter


class HeldExporter:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def export(self, spans: Sequence[FinishedSpan]) -> bool:
        self.calls += 1
        self.entered.set()
        assert self.release.wait(5)
        return True


def test_timeout_fails_and_does_not_reuse_transport_until_it_returns() -> None:
    transport = HeldExporter()
    exporter = BoundedTraceExporter(transport, timeout=0.05)
    try:
        assert exporter.export([]) is False
        assert transport.entered.is_set()
        assert exporter.export([]) is False
        assert transport.calls == 1
    finally:
        transport.release.set()

    # Wait for the transport thread to release its exclusive claim.
    for _ in range(100):
        if exporter.export([]):
            break
        threading.Event().wait(0.01)
    else:
        pytest.fail("transport never became available")
    assert transport.calls == 2


def test_transport_exceptions_reach_the_caller() -> None:
    class BrokenExporter:
        def export(self, spans: Sequence[FinishedSpan]) -> bool:
            raise ValueError("broken")

    with pytest.raises(ValueError, match="broken"):
        BoundedTraceExporter(BrokenExporter()).export([])
