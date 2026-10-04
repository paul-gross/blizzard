"""The environment-binding rules pinned by value: one holder per environment, and the instant a
release is stamped."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.runner.environments.repository import (
    EnvBinding,
    EnvironmentHeldError,
    release_instants,
    require_unheld,
)

pytestmark = pytest.mark.unit

_BOUND = datetime(2026, 1, 1, 12, tzinfo=UTC)


def _binding(chunk_id: str, environment_id: str) -> EnvBinding:
    return EnvBinding(
        chunk_id=chunk_id, environment_id=environment_id, workdir=f"/ws/{environment_id}", bound_at=_BOUND
    )


def test_require_unheld_refuses_held_by_other() -> None:
    held = [_binding("ch_other", "e1")]
    with pytest.raises(EnvironmentHeldError) as refused:
        require_unheld("ch_1", ["e2", "e1"], held)
    assert (refused.value.environment_id, refused.value.holder_chunk_id) == ("e1", "ch_other")
    require_unheld("ch_1", ["e2"], held)
    require_unheld("ch_other", ["e1"], held)
    assert EnvBinding.TRANSITIONS["free"] == frozenset({"held"})


def test_release_of_unheld_is_noop() -> None:
    later = _BOUND + timedelta(minutes=5)
    held = [_binding("ch_1", "e1")]
    assert release_instants(held, ["e1", "e2"], later) == [("e1", later)]
    assert release_instants([], ["e1"], later) == []


def test_release_clamped_to_bound_at() -> None:
    stepped_back = _BOUND - timedelta(seconds=30)
    assert _binding("ch_1", "e1").release_instant(stepped_back) == _BOUND
    assert release_instants([_binding("ch_1", "e1")], ["e1"], stepped_back) == [("e1", _BOUND)]
