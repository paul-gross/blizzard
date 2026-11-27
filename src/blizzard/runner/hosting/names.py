"""The name a hosted runner goes by, when no ``[[runner]]`` names it."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.runner.hub.identity import IReadRunnerIdentityRepository
from blizzard.runner.settings.ports import IReadRunnerSettings


@domain_model
@dataclass(frozen=True)
class RecordedName:
    """What a store records of one runner's name, unjudged: its ``name`` settings entry when that is a string, and its
    identity row's name, each ``None`` where the store holds none."""

    stored: str | None = None
    identity_name: str | None = None


def hosted_name(stored: str | None, identity_name: str | None, runner_id: str) -> str:
    """A hosted runner's name: its non-blank ``stored`` entry, stripped, else its identity row's non-blank name, else
    its id."""
    if stored is not None and stored.strip():
        return stored.strip()
    if identity_name is not None and identity_name.strip():
        return identity_name
    return runner_id


def stored_name(identity: IReadRunnerIdentityRepository, settings: IReadRunnerSettings, runner_id: str) -> str:
    """:func:`hosted_name` of what one runner's own repositories record."""
    entry = settings.snapshot().entries.get("name")
    row = identity.runner_identity()
    return hosted_name(
        entry.value if entry is not None and isinstance(entry.value, str) else None,
        row.runner_name if row is not None else None,
        runner_id,
    )
