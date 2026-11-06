"""A cadence's schedule — a five-field cron expression read as wall-clock time in an IANA zone.

The only module that reaches the cron library. It answers three questions, each a pure function of the expression,
the zone, and an instant it is handed, never a clock read: whether a schedule is valid, the latest moment at or
before an instant, and the first moment after one."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cronsim import CronSim, CronSimError

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.errors import ConfigFieldError

CRON_FIELD = "schedule.cron"
ZONE_FIELD = "schedule.zone"
#: Minute, hour, day of month, month, day of week; a sixth field the library would read as seconds.
_CRON_FIELDS = 5


@domain_model
@dataclass(frozen=True)
class Schedule:
    """A validated schedule — the only way to obtain one is :meth:`parse`."""

    cron: str
    zone: str

    @classmethod
    def parse(cls, cron: str | None, zone: str | None, *, at: datetime) -> Schedule:
        """``cron`` and ``zone`` as a schedule, refused naming the field when the expression is missing, not five
        fields, refused by the library, or names no moment in the fifty years after ``at``, or when the zone is
        missing or unknown."""
        if not cron:
            raise ConfigFieldError(CRON_FIELD, "a schedule states a cron expression")
        if len(cron.split()) != _CRON_FIELDS:
            raise ConfigFieldError(CRON_FIELD, f"a cron expression has five fields, got {cron!r}")
        if not zone:
            raise ConfigFieldError(ZONE_FIELD, "a schedule states its IANA zone")
        try:
            tz = ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigFieldError(ZONE_FIELD, f"no zone named {zone!r} is in the zone database") from exc
        try:
            iterator = CronSim(cron, at.astimezone(tz))
        except CronSimError as exc:
            raise ConfigFieldError(CRON_FIELD, f"{cron!r} is not a cron expression: {exc}") from exc
        if next(iterator, None) is None:
            raise ConfigFieldError(CRON_FIELD, f"{cron!r} names no moment in the next fifty years")
        return cls(cron, zone)

    def latest_at_or_before(self, at: datetime) -> datetime | None:
        """The latest moment at or before ``at``, in UTC; ``None`` when none falls in the fifty years before it.

        The library yields only instants strictly before the one it starts from, so it starts just past ``at``.
        Iterating backward across the hour a fall-back repeats, the library can skip the first of a fixed-hour
        moment's two wall-clock readings, so the candidate it yields is walked forward to the last moment at or
        before ``at`` — forward iteration reads both edges as Debian's cron does."""
        start = (at.astimezone(UTC).replace(microsecond=0) + timedelta(seconds=1)).astimezone(ZoneInfo(self.zone))
        candidate = next(CronSim(self.cron, start, reverse=True), None)
        if candidate is None:
            return None
        latest = candidate.astimezone(UTC)
        while (following := self.first_after(latest)) is not None and following <= at:
            latest = following
        return latest

    def first_after(self, at: datetime) -> datetime | None:
        """The first moment strictly after ``at``, in UTC; ``None`` when none falls in the fifty years after it."""
        moment = next(CronSim(self.cron, at.astimezone(ZoneInfo(self.zone))), None)
        return None if moment is None else moment.astimezone(UTC)
