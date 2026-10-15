"""The runner's wired concept stores."""

# exemplar: bzh:narrow-seams — a cut-down stand-in for ``runner/stores.py``; a real durable fact also owes its
# adapter under ``runner/store/internal/`` and a crash posture in ``crash-correctness/runner.md``.

from __future__ import annotations

from dataclasses import dataclass

from exemplar.runner.disk.model import IWriteDiskBrakeRepository


@dataclass(frozen=True)
class RunnerStores:
    """The wired concept-store collaborators, each typed to its write Protocol."""

    disk_brake: IWriteDiskBrakeRepository
