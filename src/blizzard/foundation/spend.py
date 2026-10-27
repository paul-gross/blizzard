"""The one dollar figure a spend total reads as, shared by both daemons.

Billed and estimated cost stay apart in every column; their sum exists only here. The markers are
owned by ``docs/deployment/spend.md``."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class SpendFigure:
    """A spend total as one figure: its billed sum, its estimated sum (``None`` when no row carried
    one), and whether some row carried neither."""

    cost_usd: float
    estimated_cost_usd: float | None
    partial: bool

    def amount(self) -> float:
        """Billed plus estimated."""
        return self.cost_usd + (self.estimated_cost_usd or 0.0)

    def rendered(self) -> str:
        """``$X.XX`` to the cent, ``~``-led when an estimate contributed — even one of zero — and
        ``+``-trailed when some row carried no amount at all."""
        prefix = "~" if self.estimated_cost_usd is not None else ""
        suffix = "+" if self.partial else ""
        return f"{prefix}${self.amount():.2f}{suffix}"
