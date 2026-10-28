"""The clock seams and their implementations (``bzh:injected-clock``)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from blizzard.foundation.roles import collaborator


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
    delays: list[float] = field(default_factory=list)

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


_system_clock_conforms: type[IClock] = SystemClock


_fixed_clock_conforms: type[IClock] = FixedClock


_system_monotonic_clock_conforms: type[IMonotonicClock] = SystemMonotonicClock


_manual_monotonic_clock_conforms: type[IMonotonicClock] = ManualMonotonicClock
