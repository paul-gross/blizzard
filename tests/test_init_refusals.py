from __future__ import annotations

import os
from pathlib import Path

import pytest

from blizzard.hub.config import ConfigError as HubConfigError
from blizzard.hub.runtime import init_environment as hub_init
from blizzard.runner.config import ConfigError as RunnerConfigError
from blizzard.runner.runtime import init_environment as runner_init

DAEMONS = [
    pytest.param(hub_init, HubConfigError, id="hub"),
    pytest.param(runner_init, RunnerConfigError, id="runner"),
]

skip_as_root = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the mode bits")


@pytest.mark.unit
@pytest.mark.parametrize(("init", "error"), DAEMONS)
@skip_as_root
def test_unwritable_root_refuses(tmp_path: Path, init, error) -> None:
    (tmp_path / "locked").mkdir(mode=0o500)
    with pytest.raises(error, match="cannot write"):
        init(tmp_path / "locked" / "runtime")


@pytest.mark.unit
@pytest.mark.parametrize(("init", "error"), DAEMONS)
@skip_as_root
def test_unwritable_data_dir_refuses(tmp_path: Path, init, error) -> None:
    (tmp_path / "data").mkdir(mode=0o555)
    with pytest.raises(error, match="cannot open"):
        init(tmp_path)


@pytest.mark.unit
@pytest.mark.parametrize(("init", "error"), DAEMONS)
def test_init_is_idempotent(tmp_path: Path, init, error) -> None:
    first = init(tmp_path)
    assert init(tmp_path).config_path == first.config_path
