"""The winter workspace-provider binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.runner.environments.provider.IWorkspaceProvider` by
driving the real winter CLI against a workspace root — in verification, the
``blizzard-mock`` fixture workspace (a real winter workspace over bare ``file://``
origins — ``verification.md``). ``acquire`` hands out winter's existing feature
envs (alpha, beta, …) with their worktree dirs; ``release`` returns them. Confined
to ``internal/`` (``bzh:dependency-inversion``).

**P6 contract stub.** Methods raise :class:`NotImplementedError`; the walking-
skeleton runner-track builder wires them to the winter CLI, driving the fixture
workspace so acquire/release/clean-on-acquire is *seen* working (``verification.md``).
"""

from __future__ import annotations

from blizzard.runner.environments.provider import (
    AcquiredEnvironment,
    IWorkspaceProvider,
)

_UNIMPLEMENTED = "winter workspace provider lands in the P6 walking skeleton"


class WinterWorkspaceProvider:
    """The winter binding — a NotImplemented stub until P6 wires the CLI drive."""

    def __init__(self, workspace_root: str) -> None:
        # The fixture (or real) winter workspace root this provider drives (D-019).
        self._workspace_root = workspace_root

    def acquire(self, chunk_id: str, count: int, held_ids: list[str]) -> list[AcquiredEnvironment]:
        raise NotImplementedError(_UNIMPLEMENTED)

    def release(self, environment_id: str) -> None:
        raise NotImplementedError(_UNIMPLEMENTED)


def _conforms_workspace_provider(x: WinterWorkspaceProvider) -> IWorkspaceProvider:
    return x
