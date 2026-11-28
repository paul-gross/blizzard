"""What ``HubCommand`` refuses at declaration."""

from __future__ import annotations

import click
import pytest

from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext


@pytest.mark.parametrize("name", ["project", "tenant"])
def test_a_collaborator_named_like_a_parameter_is_refused(name: str) -> None:
    with pytest.raises(ValueError, match=f"collaborators named like parameters: {name}"):
        FleetCommand("probe", params=[click.Option(["--project", "project"])], collaborators={name: CliContext})


def test_a_parameter_named_like_the_default_collaborator_is_refused() -> None:
    with pytest.raises(ValueError, match="collaborators named like parameters: cli"):
        FleetCommand("probe", params=[click.Option(["--cli", "cli"])])
