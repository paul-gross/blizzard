"""The refusal reader a work-source adapter classifies its forge's credential refusals through — the family's own
narrow view of its forge kind's refusal dialect, so the family never reaches the forge seam itself."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from blizzard.hub.domain.kernel.forge_refusals import ClassifiedRefusal


class IWorkSourceRefusalReader(Protocol):
    """One forge kind's reading of a 401 or 403 answer the rate gate declined as a rate refusal."""

    def classify(
        self,
        status: int,
        headers: Mapping[str, str],
        message: str,
        *,
        documentation_url: str | None,
        operation: str,
        endpoint: str,
        repository: str | None,
    ) -> ClassifiedRefusal:
        """Why the forge refused the credential, with its sanitized diagnostic."""
        ...
