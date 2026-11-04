"""The clock seams and their implementations (``bzh:injected-clock``)."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import collaborator, domain_model


class IClock(Protocol):
    """The time seam. Every timestamp comes from ``now()``."""

    def now(self) -> datetime: ...


class IMonotonicClock(Protocol):
    """The elapsed-time seam: a monotonic reading and the sleep that spends it. One seam,
    not two callables, so a bounded loop's delay and its clock cannot drift apart."""

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """Production clock — the real wall clock, in UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


def local_zone(environ: Mapping[str, str], localtime: Path = Path("/etc/localtime")) -> tzinfo:
    """The machine's local zone as a named zone that carries its daylight-saving rules, never a fixed offset: the
    ``TZ`` variable when it names one, else the zone ``localtime`` links to. ``UTC`` where neither resolves."""
    named = environ.get("TZ", "").lstrip(":")
    candidates = [named] if named else []
    try:
        target = localtime.resolve().as_posix()
    except OSError:
        target = ""
    if "zoneinfo/" in target:
        candidates.append(target.split("zoneinfo/", 1)[1])
    for key in candidates:
        try:
            return ZoneInfo(key)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            continue
    return UTC


class SystemMonotonicClock:
    """Production monotonic clock — ``time.monotonic`` and ``time.sleep``."""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@collaborator
@dataclass
class ManualMonotonicClock:
    """Test monotonic clock — ``sleep`` advances its own reading and records the delay,
    so a loop bounded by elapsed time terminates without really waiting."""

    reading: float = 0.0
    delays: list[float] = field(default_factory=lambda: list[float]())

    def monotonic(self) -> float:
        return self.reading

    def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.reading += seconds

    def advance(self, seconds: float) -> None:
        self.reading += seconds


@collaborator
@dataclass
class FixedClock:
    """Test clock — returns a controllable instant that ``advance`` moves."""

    instant: datetime

    def now(self) -> datetime:
        return self.instant

    def advance(self, delta: timedelta) -> None:
        self.instant += delta


class ClockBackwards(Exception):
    """``ControlledClock.set`` was asked for an instant earlier than the current reading. Not a ``ValueError``: the
    caller maps this refusal to its own status, apart from the malformed-input ones."""

    def __init__(self, current: datetime, asked: datetime) -> None:
        super().__init__(f"the clock reads {current.isoformat()}; it cannot move back to {asked.isoformat()}")
        self.current = current
        self.asked = asked


@domain_model
@dataclass(frozen=True)
class ClockMove:
    """The reading before and after a move of a ``ControlledClock``."""

    previous: datetime
    now: datetime


def _require_aware(instant: datetime, what: str) -> None:
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError(f"{what} must be timezone-aware, got naive {instant.isoformat()}")


@collaborator
class ControlledClock:
    """A clock that stands at its anchor until ``set`` or ``advance`` moves it, and only forward
    (``bzh:injected-clock``). Every read and every move, with its check, happens under one lock."""

    def __init__(self, anchor: datetime) -> None:
        _require_aware(anchor, "the anchor")
        self._lock = threading.Lock()
        self._reading = anchor

    def now(self) -> datetime:
        with self._lock:
            return self._reading

    def set(self, to: datetime) -> ClockMove:
        _require_aware(to, "the instant")
        with self._lock:
            if to < self._reading:
                raise ClockBackwards(self._reading, to)
            move = ClockMove(previous=self._reading, now=to)
            self._reading = to
            return move

    def advance(self, by: timedelta) -> ClockMove:
        if by < timedelta(0):
            raise ValueError(f"cannot advance by a negative duration, got {by}")
        with self._lock:
            move = ClockMove(previous=self._reading, now=self._reading + by)
            self._reading = move.now
            return move


#: Asks for the controlled clock with the value ``controlled``; any other value, or none, asks for nothing.
ENV_CLOCK = "BLIZZARD_CLOCK"
#: The fence (``bzh:injected-clock``): only the value ``1`` permits what ``ENV_CLOCK`` asks for.
ENV_CLOCK_FENCE = "BLIZZARD_CLOCK_FENCE"

_logger = get_logger(__name__)


@domain_model
@dataclass(frozen=True)
class ClockRequest:
    """Whether the environment asks for the controlled clock, and whether the fence permits it."""

    asked: bool
    fenced: bool

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> ClockRequest:
        return cls(asked=environ.get(ENV_CLOCK) == "controlled", fenced=environ.get(ENV_CLOCK_FENCE) == "1")


def resolve_clock(environ: Mapping[str, str]) -> ControlledClock | SystemClock:
    """The clock a serving composition root runs on, chosen once at boot: a ``ControlledClock`` anchored at the
    machine's time when it is asked for and fenced, else the ``SystemClock``. Asked for without the fence, it warns."""
    request = ClockRequest.from_environ(environ)
    if request.asked and request.fenced:
        return ControlledClock(SystemClock().now())
    if request.asked:
        _logger.warning(
            "controlled clock asked for without its fence — running on the system clock",
            clock_variable=ENV_CLOCK,
            fence_variable=ENV_CLOCK_FENCE,
        )
    return SystemClock()


_system_clock_conforms: type[IClock] = SystemClock


_fixed_clock_conforms: type[IClock] = FixedClock


_system_monotonic_clock_conforms: type[IMonotonicClock] = SystemMonotonicClock


_manual_monotonic_clock_conforms: type[IMonotonicClock] = ManualMonotonicClock


_controlled_clock_conforms: type[IClock] = ControlledClock
