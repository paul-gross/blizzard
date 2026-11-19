"""A second ``[[runner]]`` on a registered host, as a host from before its runners were store records holds one: minted
at the hub under the host's token, its token in ``.env`` and its entry in the host file, its identity left for the next
``init`` to record."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from blizzard.foundation.runner_token_names import hub_token_variable
from blizzard.runner.host_config import SCAFFOLD_SUBSCRIPTION, runner_toml, scaffold_runner
from blizzard.runner.host_file import append_runner
from blizzard.runner.hub.client import IHostRunnerAdder, IssuedIdentity
from blizzard.runner.hub.internal.token_file import HubTokenFile
from tests.support.config_inputs import load_host_config


def add_file_runner(hub: IHostRunnerAdder, root: Path, name: str, *, project: str = "default") -> str:
    """Mint ``name`` through ``hub`` and write it to the host at ``root`` as a ``[[runner]]`` with its token in
    ``.env``; its hub id."""
    config = load_host_config(root)
    issued = hub.add_runner(name, projects=(project,))
    assert isinstance(issued, IssuedIdentity), issued
    HubTokenFile.of(root, hub_token_variable(name)).write(issued.token)
    declared = {subscription.slug for subscription in config.subscriptions}
    entry = scaffold_runner(
        root,
        name,
        bindings=config.bindings_for((project,), ()),
        subscription=SCAFFOLD_SUBSCRIPTION.slug if SCAFFOLD_SUBSCRIPTION.slug in declared else None,
        runner_id=issued.runner_id,
    )
    if entry.own is not None:
        # The runner joins the host's tool profile, so a host whose runners agreed on it still does.
        entry = replace(
            entry,
            own=replace(
                entry.own,
                worker_path_prepend=config.tool_path_prepend,
                worker_env_passthrough=config.tool_env_passthrough,
            ),
        )
    append_runner(config.layout, runner_toml(entry), strict=config.strict)
    return issued.runner_id
