"""A runner names the projects it serves — the runner side.

Registration declares the configured projects (``None`` sent as JSON null, never ``[]``), and the
hub's answer is kept whole in the store: a reply answering none keeps it, an unreachable hub keeps
everything, and a reply naming another runner stores nothing. Status reads the stored answer with no
hub call. ``runner init --project`` scaffolds and adds with the projects it names. A claim's
incompatible ``409`` — with or without a ``reason`` — is read as an incompatible denial, never a lost
race."""

from __future__ import annotations

from datetime import UTC, datetime

from blizzard.runner.hub.served_projects import (
    ServedProject,
)

_AT = datetime(2026, 10, 8, 11, 0, tzinfo=UTC)


_BLIZZARD = ServedProject("blizzard", "prj_1", "blizzard", "Blizzard")


_UNRESOLVED = ServedProject("old-slug")
