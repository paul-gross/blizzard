"""Why an ingest of a runner cannot go ahead — one refusal type per reason, each naming what clears it."""

from __future__ import annotations

__all__ = [
    "BindingConflict",
    "HostNotHolding",
    "IngestRefused",
    "RunnerAlreadyHere",
    "RunnerBusy",
    "SourceMoved",
    "SourceRunning",
    "SourceUnsupported",
    "TokenConflict",
    "WorkspaceConflict",
]


class IngestRefused(Exception):
    """The ingest cannot go ahead; the message names why and what clears it. Nothing was changed unless it says so."""


class SourceRunning(IngestRefused):
    """The source, or this host, is held by a running process."""


class SourceUnsupported(IngestRefused):
    """The source is not a store ingest reads."""


class HostNotHolding(IngestRefused):
    """This host is not registered, or its hub does not hold the runner under it, or holds it in another tenant."""


class RunnerBusy(IngestRefused):
    """The runner holds an open elicitation or takeover in the source."""


class RunnerAlreadyHere(IngestRefused):
    """This host already holds a runner of that name or id, not from an ingest of this source."""


class WorkspaceConflict(IngestRefused):
    """The runner's workspace cannot stand beside this host's under the id it would take."""


class BindingConflict(IngestRefused):
    """A held binding of the runner names an environment a runner of this host holds."""


class TokenConflict(IngestRefused):
    """The runner's token cannot move: the source holds none, or this host's ``.env`` holds a different one."""


class SourceMoved(IngestRefused):
    """The source has run since the copy this host took of it."""
