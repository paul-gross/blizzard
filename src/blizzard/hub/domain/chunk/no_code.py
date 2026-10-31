"""Declaration-bound evidence that a repository required no merge."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping

from blizzard.hub.domain.artifact.model import StoredArtifact


def no_code_marker(declaration: Mapping[str, str | None]) -> tuple[str, str]:
    """Return the durable name and payload for a fully resolved commit declaration."""
    payload = {key: declaration[key] for key in ("repo", "record", "base_branch", "branch", "commit")}
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(data.encode()).hexdigest()
    return f"no-code/{digest}", data


def no_code_repos(
    artifacts: Iterable[StoredArtifact], declarations: Iterable[Mapping[str, str | None]]
) -> frozenset[str]:
    """Names whose exact current declaration has a matching, untampered marker."""
    markers = {(row.name, row.data) for row in artifacts}
    return frozenset(
        repo
        for declaration in declarations
        if (repo := declaration.get("repo")) is not None
        and all(declaration.get(key) for key in ("record", "base_branch", "branch", "commit"))
        and no_code_marker(declaration) in markers
    )
