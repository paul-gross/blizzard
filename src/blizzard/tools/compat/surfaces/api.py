"""The HTTP API surface: every operation of the hub's and the runner's OpenAPI documents."""

from __future__ import annotations

from blizzard.tools.compat.surfaces.wire import classify_spec_diff, is_any_operation
from blizzard.tools.compat.violation import Violation

SURFACE = "http-api"


def classify_api(base: dict, head: dict, *, wire: list[Violation] | None = None) -> list[Violation]:
    """Every break between two OpenAPI documents, minus the ones ``wire`` already reported.

    The wire is a subset of the hub's API, so a wire finding is reported once, under the wire."""
    reported = {(v.where, v.reason) for v in wire or ()}
    return [v for v in classify_spec_diff(base, head, is_any_operation, SURFACE) if (v.where, v.reason) not in reported]
