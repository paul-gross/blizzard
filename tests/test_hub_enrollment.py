"""RunnerEnrollmentService (unit tier) — mint/rotate a runner's bearer token (issue #86a).

A fake registry stands in for the store — only ``set_token_hash`` is meaningfully
implemented; anything else is unreachable from :meth:`RunnerEnrollmentService.enroll`
and raises loudly if a regression starts calling it (``bzh:domain-core`` — no store, no
network). Copies :mod:`tests.test_pause_service`'s fake-repo pattern.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.enrollment import RunnerEnrollmentService, hash_token
from blizzard.hub.domain.registry import IWriteRunnerRegistry, RunnerRegistration

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class _FakeRegistry:
    """Only ``set_token_hash`` is live; anything else is a bug."""

    recorded: list[tuple[str, str, datetime]] = field(default_factory=list)

    def set_token_hash(self, runner_id: str, *, token_hash: str, at: datetime) -> None:
        self.recorded.append((runner_id, token_hash, at))

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"RunnerEnrollmentService should not touch {name!r}")


def _as_write_registry(registry: _FakeRegistry) -> IWriteRunnerRegistry:
    return cast(IWriteRunnerRegistry, registry)


def _registration(runner_id: str = "runner-a") -> RunnerRegistration:
    return RunnerRegistration(
        runner_id=runner_id, workspace_id="ws-a", registered_at=_T0, last_seen_at=_T0, hub_paused=False
    )


def test_hash_token_is_the_sha256_hex_digest() -> None:
    assert hash_token("abc") == hashlib.sha256(b"abc").hexdigest()


def test_enroll_mints_a_urlsafe_token_and_stores_only_its_hash() -> None:
    clock = FixedClock(instant=_T0)
    registry = _FakeRegistry()
    service = RunnerEnrollmentService(registry=_as_write_registry(registry), clock=clock)

    token = service.enroll(_registration())

    assert len(token) >= 32  # token_urlsafe(32) -> a 43-char string; no fixed-width promise, just "long"
    assert registry.recorded == [("runner-a", hash_token(token), _T0)]
    # The plaintext never lands in what was persisted.
    assert token not in (row[1] for row in registry.recorded)


def test_enroll_mints_a_different_token_each_call() -> None:
    clock = FixedClock(instant=_T0)
    registry = _FakeRegistry()
    service = RunnerEnrollmentService(registry=_as_write_registry(registry), clock=clock)

    first = service.enroll(_registration())
    second = service.enroll(_registration())

    assert first != second


def test_re_enroll_rotates_the_stored_hash() -> None:
    """Two enrolls for the same runner append two writes; the second is what the store
    ends up holding (an overwrite, not an append-only fact — see `hub/domain/registry.py`)."""
    clock = FixedClock(instant=_T0)
    registry = _FakeRegistry()
    service = RunnerEnrollmentService(registry=_as_write_registry(registry), clock=clock)

    first_token = service.enroll(_registration())
    second_token = service.enroll(_registration())

    assert [row[0] for row in registry.recorded] == ["runner-a", "runner-a"]
    hashes = [row[1] for row in registry.recorded]
    assert hashes == [hash_token(first_token), hash_token(second_token)]
    assert hashes[0] != hashes[1]


def test_enroll_uses_the_injected_clock_not_the_wall_clock() -> None:
    later = datetime(2026, 6, 1, tzinfo=UTC)
    clock = FixedClock(instant=later)
    registry = _FakeRegistry()
    service = RunnerEnrollmentService(registry=_as_write_registry(registry), clock=clock)

    service.enroll(_registration())

    assert registry.recorded[0][2] == later
