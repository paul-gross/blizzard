"""``blizzard runner add`` binding each project to its workspace, or refusing by value."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.host_config import HostConfigError
from tests.config_inputs import load_host_config
from tests.runner_init_fakes import FakeInitHub
from tests.support_runner_add_retire import (
    _registered,  # pyright: ignore[reportPrivateUsage]
)


def test_the_host_binds_each_project_to_its_workspace_or_refuses_by_value(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    config = load_host_config(_registered(tmp_path, fake_init_hub))

    assert [(b.project, b.workspace) for b in config.bindings_for(("p", "q"), ())] == [
        ("p", "workspace-local"),
        ("q", "workspace-local"),
    ]
    with pytest.raises(HostConfigError, match="give --workspace once, or once per --project \\(2\\)"):
        config.bindings_for(("p", "q"), ("workspace-local", "workspace-local", "workspace-local"))
    with pytest.raises(HostConfigError, match="is not a workspace this host declares"):
        config.bindings_for(("p",), ("nope",))
