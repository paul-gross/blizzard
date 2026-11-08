"""The numbered pull-request and reopen markers a hub action reads and writes.

``delivery-pr/<repo>/<number>`` records the change a repository's landing opened or saw;
``park-reopen/<repo>/<number>`` records that a person closed that change and a fresh review is owed.
Pure: names, content and decisions only — the caller posts them (``bzh:hub-action-purity``)."""

from __future__ import annotations

import json
from collections.abc import Sequence

from blizzard.hub.domain.artifact.model import StoredArtifact

# A marker row as the actions see it.
PrMarker = StoredArtifact


def pr_marker_name(repo: str, number: int) -> str:
    return f"delivery-pr/{repo}/{number}"


def pr_marker_content(repo: str, number: int, url: str) -> str:
    return json.dumps({"repo": repo, "number": number, "url": url})


def latest_pr_marker(markers: Sequence[StoredArtifact], repo: str) -> StoredArtifact | None:
    """The newest numbered marker for ``repo`` in the sequence's own order; the bare form never matches."""
    prefix = f"delivery-pr/{repo}/"
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
