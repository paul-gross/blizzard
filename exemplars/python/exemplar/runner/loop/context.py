"""The loop context — the ``(stores, clock, seam clients)`` a step is a function of."""

# exemplar: bzh:narrow-seams — a cut-down stand-in: production adds each member to the real ``LoopContext`` and the
# sentinel beside its siblings.

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from exemplar.runner.disk.probe import IDiskProbe
from exemplar.runner.stores import RunnerStores


@domain_model
@dataclass(frozen=True)
class LoopConfig:
    """The reconciliation loop's static configuration."""

    spawn_root: Path
    #: The disk floor, in free bytes.
    disk_floor_bytes: int


@dataclass(frozen=True)
class LoopContext:
    """The loop driver's bundle — every step's narrow context is satisfied by it, never module-global."""

    stores: RunnerStores
    clock: IClock
    disk_probe: IDiskProbe
    config: LoopConfig


if TYPE_CHECKING:
    from exemplar.runner.disk.floor import DiskFloorContext

    _disk_floor_conforms: type[DiskFloorContext] = LoopContext
