"""The timed drivers' wait: a live interval, counted from the end of the last pass, that a wake re-evaluates and a
stop ends at once. Each driver holds its own signal, so one driver consuming its wake never clears another's."""

from __future__ import annotations

import threading
from collections.abc import Callable

import structlog

from blizzard.foundation.clock import IMonotonicClock

#: How a driver spends a wait: block on ``event`` for at most ``timeout`` seconds, returning early once it is set.
Wait = Callable[[threading.Event, float], object]


def _event_wait(event: threading.Event, timeout: float) -> object:
    return event.wait(timeout)


class WakeSignal:
    """One driver's wake: set by a write that moves its interval, and by its own stop."""

    def __init__(self) -> None:
        self.event = threading.Event()

    def set(self) -> None:
        self.event.set()


class WakeSignals:
    """Every timed driver's signal on the host: a write that moves an interval wakes them all."""

    def __init__(self) -> None:
        self._signals: list[WakeSignal] = []
        self._lock = threading.Lock()

    def signal(self) -> WakeSignal:
        """A new driver's own signal, woken with every other."""
        signal = WakeSignal()
        with self._lock:
            self._signals.append(signal)
        return signal

    def wake(self) -> None:
        with self._lock:
            signals = list(self._signals)
        for signal in signals:
            signal.set()


class IntervalWait:
    """Waits ``interval()`` seconds from a pass's end. A wake re-reads the interval: the driver runs at once when the
    new due time has passed, and waits out the rest when it has not. An interval read that fails keeps the last one,
    so a store outage never kills the driver's thread."""

    def __init__(
        self,
        interval: Callable[[], float],
        *,
        signal: WakeSignal,
        clock: IMonotonicClock,
        log: structlog.stdlib.BoundLogger,
        label: str,
        wait: Wait | None = None,
    ) -> None:
        self._interval = interval
        self._signal = signal
        self._clock = clock
        self._log = log
        self._label = label
        self._wait = wait or _event_wait
        self._stopped = False
        self._last: float | None = None

    @property
    def stopped(self) -> bool:
        return self._stopped

    def stop(self) -> None:
        self._stopped = True
        self._signal.set()

    def interval(self) -> float:
        try:
            self._last = self._interval()
        except Exception as exc:  # a failed read keeps the last interval rather than ending the driver
            if self._last is None:
                raise
            self._log.warning(f"{self._label} interval read failed; keeping the last", detail=str(exc))
        return self._last

    def until_due(self, since: float, seconds: Callable[[], float] | None = None) -> bool:
        """Wait from ``since`` until ``seconds()`` (the live interval by default) has elapsed: ``True`` when due,
        ``False`` once stopped."""
        read = seconds or self.interval
        while not self._stopped:
            remaining = since + read() - self._clock.monotonic()
            if remaining <= 0:
                return True
            self._wait(self._signal.event, remaining)
            # Cleared before the next read: a write committed after it sets the signal again.
            self._signal.event.clear()
        return False
