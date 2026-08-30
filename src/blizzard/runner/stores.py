"""The runner-store bundle (blizzard#410, D4).

A frozen bundle of the write-capable Protocol seams :mod:`~blizzard.runner.composition`
builds, for a collaborator spanning several concepts rather than just one or two. Every
field is the **write** variant — narrowing one to read-only is the sibling issue this
bundle does not take on."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.runner.domain.leases import IWriteLeaseRepository


@dataclass(frozen=True)
class RunnerStores:
    """The wired concept-store collaborators, built by
    :func:`~blizzard.runner.composition.build_stores`."""

    leases: IWriteLeaseRepository
