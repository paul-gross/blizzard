"""The committed surface documents: each one's path, the surface(s) it carries, and the classifier that diffs it."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from blizzard.foundation.roles import domain_model
from blizzard.tools.compat.surfaces.api import classify_api
from blizzard.tools.compat.surfaces.cli import classify_cli
from blizzard.tools.compat.surfaces.config import classify_config
from blizzard.tools.compat.surfaces.egress import classify_egress
from blizzard.tools.compat.surfaces.sse import classify_sse
from blizzard.tools.compat.surfaces.store import classify_store
from blizzard.tools.compat.surfaces.traces import classify_traces
from blizzard.tools.compat.surfaces.wire import classify_spec_diff
from blizzard.tools.compat.violation import Violation

type Classifier = Callable[[dict[str, Any], dict[str, Any]], list[Violation]]

#: Reads one committed file as text, or ``None`` where the tree has no such path.
type Fetch = Callable[[str], str | None]

#: The surface name the store schema documents carry.
STORE_SURFACE = "store"


def _classify_hub_api(base: dict[str, Any], head: dict[str, Any]) -> list[Violation]:
    wire = classify_spec_diff(base, head)
    return [*wire, *classify_api(base, head, wire=wire)]


@domain_model
@dataclass(frozen=True)
class Document:
    """One committed document, the surface(s) it carries, and the classifier that diffs it.

    ``path`` is a single JSON file. A document ``composed`` of several files names its manifest as ``path``: it is
    read as ``{"scope": ..., "manifest": ..., "cases": {kind: {case: payload}}}``, with one ``<kind>.json`` beside
    the manifest for each kind it lists, and it covers the ``*.json`` files directly in that directory."""

    path: str
    surface: str
    classify: Classifier
    scope: str | None = None

    def is_composed(self) -> bool:
        return self.scope is not None

    def covers(self, path: str) -> bool:
        """Whether a change to ``path`` changes this document."""
        if not self.is_composed():
            return path == self.path
        directory, _, name = path.rpartition("/")
        return directory == self.path.rpartition("/")[0] and name.endswith(".json")

    def load(self, fetch: Fetch) -> dict[str, Any] | None:
        """The document as ``fetch`` reads it, or ``None`` where its file (its manifest, when composed) is absent."""
        text = fetch(self.path)
        if text is None:
            return None
        document: dict[str, Any] = json.loads(text)
        if not self.is_composed():
            return document
        directory = self.path.rpartition("/")[0]
        cases = {kind: json.loads(fetch(f"{directory}/{kind}.json") or "{}") for kind in document.get("kinds", [])}
        return {"scope": self.scope, "manifest": document, "cases": cases}


DOCUMENTS: tuple[Document, ...] = (
    Document("openapi/hub.openapi.json", "wire, http-api", _classify_hub_api),
    Document("openapi/runner.openapi.json", "http-api", classify_api),
    Document("contracts/sse/manifest.json", "event-stream", classify_sse, scope="hub"),
    Document("contracts/sse/runner/manifest.json", "event-stream", classify_sse, scope="runner"),
    Document("contracts/cli/hub.json", "cli", classify_cli),
    Document("contracts/cli/runner.json", "cli", classify_cli),
    Document("contracts/config/hub.json", "config", classify_config),
    Document("contracts/config/host.json", "config", classify_config),
    Document("contracts/traces/dictionary.json", "trace", classify_traces),
    Document("contracts/egress/dictionary.json", "egress", classify_egress),
    Document("contracts/store/hub.json", STORE_SURFACE, classify_store),
    Document("contracts/store/runner.json", STORE_SURFACE, classify_store),
)
