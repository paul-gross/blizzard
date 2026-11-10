"""``Session``'s pure expiry and touch rules over an explicit instant (unit tier)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from blizzard.hub.auth.models import Session

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_IDLE = timedelta(hours=1)
_MAX = timedelta(days=1)
_GRAIN = timedelta(minutes=5)


def _session(*, expires_at: datetime = _T0 + _IDLE, last_seen_at: datetime = _T0) -> Session:
    return Session(id_hash="h", user_id="u", created_at=_T0, expires_at=expires_at, last_seen_at=last_seen_at)


def test_expired_at_the_idle_boundary() -> None:
    session = _session()
    assert not session.expired(_T0 + _IDLE - timedelta(seconds=1), absolute_max_age=_MAX)
    assert session.expired(_T0 + _IDLE, absolute_max_age=_MAX)


def test_expired_at_the_absolute_boundary() -> None:
    session = _session(expires_at=_T0 + _MAX + _IDLE)
    assert not session.expired(_T0 + _MAX - timedelta(seconds=1), absolute_max_age=_MAX)
    assert session.expired(_T0 + _MAX, absolute_max_age=_MAX)


def test_touch_due_at_the_granularity_boundary() -> None:
    session = _session()
    assert not session.touch_due(_T0 + _GRAIN - timedelta(seconds=1), touch_granularity=_GRAIN)
    assert session.touch_due(_T0 + _GRAIN, touch_granularity=_GRAIN)


def test_slid_expiry_is_a_fresh_idle_window() -> None:
    now = _T0 + timedelta(minutes=10)
    assert _session().slid_expiry(now, idle_ttl=_IDLE, absolute_max_age=_MAX) == now + _IDLE


def test_slid_expiry_is_capped_at_the_absolute_maximum() -> None:
    now = _T0 + _MAX - timedelta(minutes=10)
    assert _session().slid_expiry(now, idle_ttl=_IDLE, absolute_max_age=_MAX) == _T0 + _MAX
