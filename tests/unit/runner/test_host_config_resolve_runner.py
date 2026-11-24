"""Which runner a stopped-host verb means, decided on the host file and the store's roster alone."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.config_table import ConfigError
from blizzard.runner.hosting.model import HostedRoster
from blizzard.runner.hosting.roster import HostRoster
from tests.support.config_inputs import load_host_config
from tests.support.host_files import TEST_RUNNER_ID, write_minimal_host


def _roster(*, active: tuple[str, ...] = (), retired: frozenset[str] = frozenset(), **names: str) -> HostRoster:
    return HostRoster(
        named=(*active, *retired),
        hosted=frozenset((*active, *retired)),
        split=HostedRoster(active=active, retired=retired),
        names=names,
    )


def test_a_declared_runner_is_the_one_meant_while_the_store_does_not_hold_it_retired(tmp_path: Path) -> None:
    config = load_host_config(write_minimal_host(tmp_path).parent)
    roster = _roster(active=(TEST_RUNNER_ID, "rn_other"), rn_other="other")
    assert config.resolve_runner(roster, None).id == TEST_RUNNER_ID
    assert config.resolve_runner(None, "runner-local").id == TEST_RUNNER_ID


def test_a_retired_declared_runner_yields_to_the_stores_only_active_runner(tmp_path: Path) -> None:
    config = load_host_config(write_minimal_host(tmp_path).parent)
    roster = _roster(active=("rn_other",), retired=frozenset({TEST_RUNNER_ID}), rn_other="other")
    assert config.resolve_runner(roster, None).id == "rn_other"
    assert config.resolve_runner(roster, "other").id == "rn_other"
    assert config.resolve_runner(roster, "rn_other").name == "other"


def test_a_retired_declared_runner_stays_meant_when_the_store_names_no_match(tmp_path: Path) -> None:
    config = load_host_config(write_minimal_host(tmp_path).parent)
    roster = _roster(active=("rn_other",), retired=frozenset({TEST_RUNNER_ID}), rn_other="other")
    assert config.resolve_runner(roster, "runner-local").id == TEST_RUNNER_ID


def test_several_store_runners_are_refused_naming_each(tmp_path: Path) -> None:
    config = load_host_config(write_minimal_host(tmp_path).parent)
    roster = _roster(active=("rn_a", "rn_b"), retired=frozenset({TEST_RUNNER_ID}), rn_a="a", rn_b="b")
    with pytest.raises(ConfigError, match=r"hosts several runners \(a \(rn_a\), b \(rn_b\)\)"):
        config.resolve_runner(roster, None)


def test_a_store_runner_is_found_by_name_when_the_file_names_none_of_that_name(tmp_path: Path) -> None:
    config = load_host_config(write_minimal_host(tmp_path).parent)
    roster = _roster(active=(TEST_RUNNER_ID, "rn_other"), rn_other="other")
    with pytest.raises(ConfigError, match="declares no runner 'other'"):
        config.resolve_runner(None, "other")
    assert config.resolve_runner(roster, "other").id == "rn_other"
    with pytest.raises(ConfigError, match="declares no runner 'ghost'"):
        config.resolve_runner(roster, "ghost")
