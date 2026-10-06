"""RunnerEnrollmentService (unit tier) — mint/rotate a runner's bearer token.

A fake registry stands in for the store — only ``rotate_token`` is meaningfully
implemented; anything else raises loudly if called (``bzh:domain-core``)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.tokens import TokenHash
from blizzard.hub.domain.runners.enrollment import RunnerEnrollmentService
from blizzard.hub.domain.runners.registration import IWriteRunnerRegistry, RunnerRegistration, TokenRotation

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class _FakeRegistry:
    """Only ``rotate_token`` is live; anything else is a bug."""

    recorded: list[tuple[str, str, datetime]] = field(default_factory=list)

    def rotate_token(self, rotation: TokenRotation) -> int | None:
        self.recorded.append((rotation.runner_id, rotation.token_hash, rotation.at))
        return None

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"RunnerEnrollmentService should not touch {name!r}")


def _as_write_registry(registry: _FakeRegistry) -> IWriteRunnerRegistry:
    return cast(IWriteRunnerRegistry, registry)


def _registration(runner_id: str = "runner-a") -> RunnerRegistration:
    return RunnerRegistration(
        runner_id=runner_id,
        name=runner_id,
        added_at=_T0,
        workspace_id="ws-a",
        registered_at=_T0,
        last_seen_at=_T0,
        hub_paused=False,
    )


def test_token_hash_is_the_sha256_hex_digest() -> None:
    assert TokenHash("abc").hex == hashlib.sha256(b"abc").hexdigest()


def test_enroll_mints_a_urlsafe_token_and_stores_only_its_hash() -> None:
    clock = FixedClock(instant=_T0)
    registry = _FakeRegistry()
    service = RunnerEnrollmentService(registry=_as_write_registry(registry), clock=clock)

    token = service.enroll(_registration())

    assert len(token) >= 32  # token_urlsafe(32) -> a 43-char string; no fixed-width promise, just "long"
    assert registry.recorded == [("runner-a", TokenHash(token).hex, _T0)]
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
    ends up holding (an overwrite, not an append-only fact — see `hub/domain/runners/registration.py`)."""
    clock = FixedClock(instant=_T0)
    registry = _FakeRegistry()
    service = RunnerEnrollmentService(registry=_as_write_registry(registry), clock=clock)

    first_token = service.enroll(_registration())
    second_token = service.enroll(_registration())

    assert [row[0] for row in registry.recorded] == ["runner-a", "runner-a"]
    hashes = [row[1] for row in registry.recorded]
    assert hashes == [TokenHash(first_token).hex, TokenHash(second_token).hex]
    assert hashes[0] != hashes[1]


def test_enroll_uses_the_injected_clock_not_the_wall_clock() -> None:
    later = datetime(2026, 6, 1, tzinfo=UTC)
    clock = FixedClock(instant=later)
    registry = _FakeRegistry()
    service = RunnerEnrollmentService(registry=_as_write_registry(registry), clock=clock)

    service.enroll(_registration())

    assert registry.recorded[0][2] == later
