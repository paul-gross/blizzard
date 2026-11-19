"""The hosted-runner rules, by value: retirement is final, a name is unique among the host's active runners, and only
``init``'s join and the seed stand for a ``[[runner]]``."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.runner.hosting.model import (
    CLI,
    JOIN,
    OPERATOR,
    SEED,
    HostedRunner,
    HostedRunnerDoor,
    HostedRunnerFact,
    HostedRunnerOp,
    HostedRunnerState,
    hosted_beside_the_file,
    name_taken,
)

_AT = datetime(2026, 11, 19, tzinfo=UTC)


def _fact(op: HostedRunnerOp, door: HostedRunnerDoor = "api", actor: str = OPERATOR) -> HostedRunnerFact:
    return HostedRunnerFact(runner_id="rn_1", recorded_at=_AT, op=op, actor=actor, door=door)


def test_a_runner_is_active_once_added_and_retired_for_good_once_retired() -> None:
    added, retired = _fact(HostedRunnerOp.ADDED), _fact(HostedRunnerOp.RETIRED)
    assert HostedRunner("rn_1", ()).state is None
    assert HostedRunner("rn_1", (added,)).state is HostedRunnerState.ACTIVE
    assert HostedRunner("rn_1", (added, retired)).retired()
    assert HostedRunner("rn_1", (added, retired, added)).retired()


@pytest.mark.parametrize(
    ("name", "active", "refused"),
    [
        ("night-1", {"day-1"}, None),
        ("night-1", {"night-1"}, "already holds a runner named 'night-1'"),
        (" night-1 ", {"night-1"}, "already holds a runner named 'night-1'"),
        ("  ", set[str](), "non-blank"),
    ],
)
def test_a_name_is_unique_among_the_hosts_active_runners(name: str, active: set[str], refused: str | None) -> None:
    taken = name_taken(name, active)
    if refused is None:
        assert taken is None
    else:
        assert taken is not None and refused in taken


@pytest.mark.parametrize(
    ("door", "actor", "beside"),
    [
        (SEED, SEED, False),
        (CLI, JOIN, False),
        (CLI, OPERATOR, True),
        ("api", OPERATOR, True),
        ("page", "alice", True),
    ],
)
def test_only_the_seed_and_inits_join_stand_for_a_file_runner(door: HostedRunnerDoor, actor: str, beside: bool) -> None:
    assert hosted_beside_the_file((_fact(HostedRunnerOp.ADDED, door, actor),)) is beside
    assert not hosted_beside_the_file(())
