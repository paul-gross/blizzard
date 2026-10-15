"""The reconciliation loop's phases."""

# exemplar: bzh:steppable-loop — a stand-in for a ``Step`` subclass in ``runner/loop/steps.py``, which production
# registers with ``_traced`` in ``runner/loop/tick.py``; the phase makes one call into its step module.

from __future__ import annotations

from dataclasses import dataclass

from exemplar.runner.disk.floor import DiskFloorCheck
from exemplar.runner.loop.context import LoopContext


@dataclass(frozen=True)
class DiskFloor:
    """The disk-floor check, once per tick."""

    ctx: LoopContext

    def run(self) -> None:
        DiskFloorCheck(self.ctx).run()
