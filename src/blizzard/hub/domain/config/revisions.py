"""The revisions read: a configured record's revision and its secret's, for one record.

An object built from a record is current exactly while these match the ones it was built
under (``bzh:config-read-on-use``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.changes import RecordKind


@domain_model
@dataclass(frozen=True)
class ConfigRevisions:
    """A record's revision with the name and revision of the secret it references.

    A secret's own revisions name itself; a record with no secret carries ``None`` for both."""

    revision: int
    secret_name: str | None
    secret_revision: int | None


class IReadConfigRevisions(Protocol):
    def revisions(self, kind: RecordKind, key: str) -> ConfigRevisions | None:
        """One indexed read, retired records included; ``None`` when no such record exists."""
        ...
