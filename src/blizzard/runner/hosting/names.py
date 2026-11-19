"""The name a hosted runner goes by, when no ``[[runner]]`` names it."""

from __future__ import annotations

from blizzard.runner.hub.identity import IReadRunnerIdentityRepository
from blizzard.runner.settings.ports import IReadRunnerSettings


def stored_name(identity: IReadRunnerIdentityRepository, settings: IReadRunnerSettings, runner_id: str) -> str:
    """A hosted runner's name: its stored ``name`` entry, else its identity row's, else its id."""
    stored = settings.snapshot().entries.get("name")
    if stored is not None and isinstance(stored.value, str) and stored.value.strip():
        return stored.value.strip()
    row = identity.runner_identity()
    return row.runner_name if row is not None else runner_id
