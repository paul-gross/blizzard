"""The committed surface documents: each one's path, the surface(s) it carries, and the classifier that diffs it."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from blizzard.foundation.roles import domain_model
from blizzard.tools.compat.surfaces.api import classify_api
from blizzard.tools.compat.surfaces.config import classify_config
from blizzard.tools.compat.surfaces.egress import classify_egress
from blizzard.tools.compat.surfaces.store import classify_store
from blizzard.tools.compat.surfaces.traces import classify_traces
from blizzard.tools.compat.surfaces.wire import classify_spec_diff
from blizzard.tools.compat.violation import Violation

type Classifier = Callable[[dict[str, Any], dict[str, Any]], list[Violation]]

#: The surface name the store schema documents carry.
STORE_SURFACE = "store"


def _classify_hub_api(base: dict[str, Any], head: dict[str, Any]) -> list[Violation]:
    wire = classify_spec_diff(base, head)
    return [*wire, *classify_api(base, head, wire=wire)]


@domain_model
@dataclass(frozen=True)
class Document:
    """One committed document, the surface(s) it carries, and the classifier that diffs it."""

    path: str
    surface: str
    classify: Classifier


DOCUMENTS: tuple[Document, ...] = (
    Document("openapi/hub.openapi.json", "wire, http-api", _classify_hub_api),
    Document("openapi/runner.openapi.json", "http-api", classify_api),
    Document("contracts/config/hub.json", "config", classify_config),
    Document("contracts/config/host.json", "config", classify_config),
    Document("contracts/traces/dictionary.json", "trace", classify_traces),
    Document("contracts/egress/dictionary.json", "egress", classify_egress),
    Document("contracts/store/hub.json", STORE_SURFACE, classify_store),
    Document("contracts/store/runner.json", STORE_SURFACE, classify_store),
)
