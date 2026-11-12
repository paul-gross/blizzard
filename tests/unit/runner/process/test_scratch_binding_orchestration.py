from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any

import pytest

from blizzard.runner.process.scratch_binding import RunnerScratchLeases, bind_scratch_root


class _NothingClosed:
    def closed_lease_ids(self, lease_ids: Collection[str]) -> set[str]:
        return set()


class _Port:
    def __init__(self, previous: Path | None, *, fail_probe_on: Path | None = None) -> None:
        self.previous, self.fail_probe_on = previous, fail_probe_on
        self.events: list[tuple[str, Path]] = []

    location = "/host/data/worker-scratch-root"

    def prepare(self, root: Path) -> None:
        self.events.append(("prepare", root))

    def probe(self, root: Path) -> None:
        if root == self.fail_probe_on:
            raise OSError("interrupted")
        self.events.append(("probe", root))

    def read_previous(self) -> Path | None:
        return self.previous

    def publish(self, root: Path) -> None:
        self.events.append(("publish", root))
        self.previous = root


class _Sweeper:
    def __init__(self, log: list[Any], root: str, runner_id: str) -> None:
        self.log, self.root, self.runner_id = log, root, runner_id

    def sweep_orphans(self, active_lease_ids: set[str], *, strict: bool = False) -> int:
        self.log.append(("orphans", self.root, self.runner_id, sorted(active_lease_ids), strict))
        return 0

    def sweep_legacy(self, removable: Callable[[str], bool], *, strict: bool = False) -> int:
        return 0


def _bind(port: _Port, root: Path, *, active: tuple[str, ...] = ()) -> list[Any]:
    log: list[Any] = []

    def factory(root: str, *, runner_id: str, legacy_root: str) -> _Sweeper:
        return _Sweeper(log, root, runner_id)

    bind_scratch_root(
        port,
        root,
        Path("/legacy"),
        {"rn_a": RunnerScratchLeases(active, _NothingClosed())},
        factory,
    )
    return log


def test_a_changed_root_reconciles_the_previous_root_before_publishing_then_the_new_one() -> None:
    old, new = Path("/s/old"), Path("/s/new")
    port = _Port(old)

    log = _bind(port, new)

    assert port.events == [("prepare", new), ("probe", old), ("publish", new), ("probe", new)]
    assert [entry[1] for entry in log] == [str(old), str(new)]


def test_active_leases_refuse_a_changed_root_before_anything_is_reconciled() -> None:
    port = _Port(Path("/s/old"))

    with pytest.raises(RuntimeError, match="drain leases"):
        _bind(port, Path("/s/new"), active=("lease_1",))

    assert ("publish", Path("/s/new")) not in port.events
    assert not any(event[0] == "probe" for event in port.events)


def test_an_invalid_previous_binding_is_refused() -> None:
    with pytest.raises(RuntimeError, match="Invalid scratch root binding in /host/data/worker-scratch-root"):
        _bind(_Port(Path("relative")), Path("/s/new"))


def test_a_failed_reconciliation_of_the_previous_root_publishes_nothing() -> None:
    old = Path("/s/old")
    port = _Port(old, fail_probe_on=old)

    with pytest.raises(OSError, match="interrupted"):
        _bind(port, Path("/s/new"))

    assert not any(event[0] == "publish" for event in port.events)


def test_an_unchanged_root_is_only_reconciled_and_passes_active_leases_through() -> None:
    root = Path("/s/root")
    port = _Port(root)

    log = _bind(port, root, active=("lease_1",))

    assert port.events == [("prepare", root), ("probe", root)]
    assert log == [("orphans", str(root), "rn_a", ["lease_1"], True)]
