from __future__ import annotations

import os
from pathlib import Path

import pytest

from blizzard.hub.config import ConfigError as HubConfigError
from blizzard.runner.config import ConfigError as RunnerConfigError
from tests.config_inputs import init_host, init_hub

DAEMONS = [
    pytest.param(init_hub, HubConfigError, id="hub"),
    pytest.param(init_host, RunnerConfigError, id="runner"),
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
