"""The stage-time check of a garden artifact — whether content a worker is about to stage under ``name``
meets the format a delivery arg of the chunk's graph applies to it. The verdict is the one delivery would
reach: each format is judged by the same :class:`~blizzard.hub.domain.garden.formats.IGardenFormats` method
delivery calls, so the two messages are identical by construction. The rule — which formats a name owes —
is :meth:`~blizzard.hub.domain.graph.model.Graph.artifact_formats`'s; this module only orders the calls."""

from __future__ import annotations

from blizzard.hub.domain.garden.delivery.validation import GardenDeliveryRejected
from blizzard.hub.domain.garden.formats import IGardenFormats
from blizzard.hub.domain.graph.actions import ArtifactFormat, HubActionCatalogue
from blizzard.hub.domain.graph.model import Graph

# Judged in a fixed order so a name owing both formats refuses the same way each time.
_ORDER: tuple[ArtifactFormat, ...] = (ArtifactFormat.FINDING_DELTA, ArtifactFormat.GARDEN_PROPOSAL_CANDIDATES)


class GardenStageCheck:
    """Judges content offered for staging against the formats its graph owes the name."""

    def __init__(self, *, formats: IGardenFormats, catalogue: HubActionCatalogue) -> None:
        self._formats = formats
        self._catalogue = catalogue

    def refusal(self, graph: Graph, name: str, content: str) -> str | None:
        """The delivery-time rejection message for ``content`` under ``name``, or ``None`` when it passes
        or ``name`` owes no format."""
        owed = graph.artifact_formats(name, self._catalogue)
        try:
            for fmt in _ORDER:
                if fmt not in owed:
                    continue
                if fmt is ArtifactFormat.FINDING_DELTA:
                    self._formats.finding_delta(name, content)
                else:
                    self._formats.proposal_candidates(name, content)
        except GardenDeliveryRejected as exc:
            return str(exc)
        return None
