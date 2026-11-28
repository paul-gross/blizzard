"""What ``ContainerFleetCommand`` refuses at declaration."""

from __future__ import annotations

import click
import pytest

from blizzard.hub.cli.container_command import ContainerFleetCommand
from blizzard.hub.cli.context import CliContext


@pytest.mark.parametrize("name", ["project", "tenant"])
def test_a_collaborator_named_like_a_parameter_is_refused(name: str) -> None:
    with pytest.raises(ValueError, match=f"collaborators named like parameters: {name}"):
        ContainerFleetCommand(
            "probe", params=[click.Option(["--project", "project"])], collaborators={name: CliContext}
        )
