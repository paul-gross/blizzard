"""Bound a runner's wait for synchronous trace transport without sharing an in-flight exporter."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from concurrent.futures import Future, TimeoutError

from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.foundation.trace_spans import FinishedSpan


class BoundedTraceExporter:
    """A timed-out batch fails; its transport retains exclusive use of the exporter until it returns."""

    def __init__(self, exporter: ITraceExporter, *, timeout: float = 8.0) -> None:
        self._exporter = exporter
        self._timeout = timeout
        self._lock = threading.Lock()

    def export(self, spans: Sequence[FinishedSpan]) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        result: Future[bool] = Future()

        def send() -> None:
            try:
                result.set_result(self._exporter.export(spans))
            except BaseException as exc:
                result.set_exception(exc)
            finally:
                self._lock.release()

        try:
            threading.Thread(target=send, name="blizzard-trace-transport", daemon=True).start()
        except BaseException:
            self._lock.release()
            raise
        try:
            return result.result(timeout=self._timeout)
        except TimeoutError:
            return False
