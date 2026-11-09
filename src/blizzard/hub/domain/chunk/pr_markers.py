"""The ``delivery-pr/`` and ``park-reopen/`` marker grammar, in one place.

``delivery-pr/<repo>[/<number>]`` records the change a repository's landing opened or saw;
``park-reopen/<repo>/<number>`` records that a person closed that change and a fresh review is owed.
Names, the JSON content's encode and decode, and the parsed :class:`PrMarker` live here and nowhere else. Pure:
names, content and decisions only — the caller posts them (``bzh:hub-action-purity``)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.artifact.model import StoredArtifact

#: The family prefix every ``delivery-pr/`` marker name starts with.
PR_MARKER_PREFIX = "delivery-pr/"


@domain_model
@dataclass(frozen=True)
class PrMarker:
    """One well-formed ``delivery-pr/<repo>[/<number>]`` marker: the artifact ``name`` it was written under,
    its ``epoch``, and the PR its payload names. A marker whose payload is not that shape never becomes one."""

    name: str
    epoch: int
    repo: str
    number: int
    url: str


def pr_marker_bare_name(repo: str) -> str:
    """The bare form, ``delivery-pr/<repo>``, without a number."""
    return f"{PR_MARKER_PREFIX}{repo}"


def pr_marker_name(repo: str, number: int) -> str:
    return f"{PR_MARKER_PREFIX}{repo}/{number}"


def is_pr_marker_name(marker: PrMarker) -> bool:
    """Whether ``marker`` was written under one of the two name forms its own ``repo`` and ``number`` spell."""
    return marker.name in {pr_marker_bare_name(marker.repo), pr_marker_name(marker.repo, marker.number)}


def pr_marker_content(repo: str, number: int, url: str) -> str:
    return json.dumps({"repo": repo, "number": number, "url": url})


def decode_pr_marker(name: str, epoch: int, data: str) -> PrMarker | None:
    """The PR a ``delivery-pr/`` marker's JSON payload names, or ``None`` when it is not that shape."""
    try:
        loaded: object = json.loads(data)
    except ValueError:
        return None
    if not isinstance(loaded, dict):
        return None
    payload = cast(dict[str, Any], loaded)
    if not (
        isinstance(payload.get("repo"), str)
        and isinstance(payload.get("number"), int)
        and isinstance(payload.get("url"), str)
    ):
        return None
    return PrMarker(name=name, epoch=epoch, repo=payload["repo"], number=payload["number"], url=payload["url"])


def latest_pr_marker(markers: Sequence[StoredArtifact], repo: str) -> StoredArtifact | None:
    """The newest numbered marker for ``repo`` in the sequence's own order; the bare form never matches."""
    prefix = f"{PR_MARKER_PREFIX}{repo}/"
    return next((row for row in reversed(markers) if row.name.startswith(prefix)), None)


def _suffix(marker: StoredArtifact) -> str:
    return marker.name.rsplit("/", 1)[1]


def pr_marker_number(marker: StoredArtifact) -> int:
    return int(_suffix(marker))


def reopen_marker_name(repo: str, marker: StoredArtifact) -> str:
    """The reopen marker for the change ``marker`` names, its number kept as written."""
    return f"park-reopen/{repo}/{_suffix(marker)}"


def reopen_marker_content() -> str:
    return ""


def is_reopened(markers: Sequence[StoredArtifact], repo: str, marker: StoredArtifact, epoch: int) -> bool:
    """Whether ``markers`` hold a reopen for ``marker``'s change written in ``epoch``."""
    name = reopen_marker_name(repo, marker)
    return any(row.name == name and row.epoch == epoch for row in markers)
