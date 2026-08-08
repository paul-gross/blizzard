"""Unit coverage for the crash-point registry (``bzh:crash-point-registry``).

Exercises the mechanism in-process (no subprocess kill): the registry enumerates,
``reached()`` is a no-op unless armed **and** fenced, and every declared point is
uniquely named. The actual SIGKILL is exercised by the kill-9 sweep (``tests/crash``).
"""

from __future__ import annotations

import os

import pytest

from blizzard.foundation import crash

pytestmark = pytest.mark.unit


def test_registry_enumerates_the_daemon_loop_points() -> None:
    """Returns a non-empty, name-sorted list of crash points."""
    points = crash.CrashPoint.discover()
    names = [p.name for p in points]
    assert names == sorted(names), "points must enumerate name-sorted for a stable sweep order"
    # The registry spans both daemons' dangerous windows.
    assert any(n.startswith("reap.") for n in names)
    assert any(n.startswith("fill.") for n in names)
    assert any(n.startswith("advance.") for n in names)
    assert any(n.startswith("flush.") for n in names)
    assert any(n.startswith("hubnode.") for n in names)


def test_duplicate_declaration_is_rejected() -> None:
    """A point name is declared exactly once — a duplicate would make the sweep ambiguous."""
    existing = crash.CrashPoint.all()[0].name
    with pytest.raises(ValueError, match="duplicate crash point"):
        crash.CrashPoint.declare(existing)


def test_reached_is_a_noop_when_unarmed() -> None:
    """With no point armed, reaching any boundary does nothing (zero-overhead unarmed)."""
    point = crash.CrashPoint.all()[0]
    prior = dict(os.environ)
    try:
        os.environ.pop(crash.ENV_CRASH_POINT, None)
        os.environ.pop(crash.ENV_CRASH_FENCE, None)
        crash.CrashPoint.rearm_from_env()
        point.reached()  # must return without terminating the test process
    finally:
        os.environ.clear()
        os.environ.update(prior)
        crash.CrashPoint.rearm_from_env()


def test_reached_is_a_noop_when_armed_but_unfenced() -> None:
    """Self-fencing: an armed point with no fence never fires (can't crash production)."""
    point = crash.CrashPoint.all()[0]
    prior = dict(os.environ)
    try:
        os.environ[crash.ENV_CRASH_POINT] = point.name
        os.environ.pop(crash.ENV_CRASH_FENCE, None)  # armed but NOT fenced
        crash.CrashPoint.rearm_from_env()
        point.reached()  # the fence is off, so this must not SIGKILL
    finally:
        os.environ.clear()
        os.environ.update(prior)
        crash.CrashPoint.rearm_from_env()


def test_reached_is_a_noop_for_a_different_armed_point() -> None:
    """Arming point A (fenced) leaves point B inert — only the named boundary fires."""
    a, b = crash.CrashPoint.all()[0], crash.CrashPoint.all()[1]
    prior = dict(os.environ)
    try:
        os.environ[crash.ENV_CRASH_POINT] = a.name
        os.environ[crash.ENV_CRASH_FENCE] = "1"
        crash.CrashPoint.rearm_from_env()
        b.reached()  # a different point is armed, so b must not fire
    finally:
        os.environ.clear()
        os.environ.update(prior)
        crash.CrashPoint.rearm_from_env()
