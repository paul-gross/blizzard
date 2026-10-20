"""Which seeded values a fold regenerates, and the one rule the proof reads them by.

A history seeds rows whose values no two runs agree on: ids minted from the clock and ``os.urandom``, and the
instants those rows are stamped with. Freezing them would hand every fresh install one tenant id and a fold-dated
``created_at``, so the baseline mints a fresh id and stamps a fresh instant at upgrade time instead, and the
comparison of a baseline's rows with a history's reads exactly those two kinds up to what they cannot agree on."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

type Row = Mapping[str, Any]
type Rows = Mapping[str, Sequence[Row]]
type Kind = Literal["timestamp", "minted", "literal"]

#: ``<prefix>_<26-char Crockford ULID>``, as ``blizzard.foundation.ids`` mints; the prefix is lowercase letters.
MINTED_ID = re.compile(r"(?<![A-Za-z0-9])([a-z]{2,6})_[0-9A-HJKMNP-TV-Z]{26}(?![A-Za-z0-9])")

TIMESTAMP_PLACEHOLDER = "<timestamp>"


def classify(value: object) -> Kind:
    """Whether ``value`` is regenerated as an instant, regenerated as a string holding minted ids, or kept."""
    if isinstance(value, datetime):
        return "timestamp"
    if isinstance(value, str) and MINTED_ID.search(value):
        return "minted"
    return "literal"


def _masked(row: Row) -> str:
    return repr({name: _mask(value) for name, value in sorted(row.items())})


def _mask(value: object) -> object:
    kind = classify(value)
    if kind == "timestamp":
        return TIMESTAMP_PLACEHOLDER
    if kind == "minted":
        return MINTED_ID.sub(lambda found: f"{found.group(1)}_*", str(value))
    return value


def normalize(rows: Rows) -> dict[str, list[dict[str, Any]]]:
    """``rows`` with every minted id renamed ``<prefix>#<n>`` by first appearance and every instant by presence.

    Rows are ordered by their masked content first, so two stores minting different ids for the same seed rows
    normalize identically, and a reference between rows keeps pointing at the row it did."""
    names: dict[str, str] = {}
    counters: defaultdict[str, int] = defaultdict(int)

    def rename(found: re.Match[str]) -> str:
        old = found.group(0)
        if old not in names:
            counters[found.group(1)] += 1
            names[old] = f"{found.group(1)}#{counters[found.group(1)]}"
        return names[old]

    normalized: dict[str, list[dict[str, Any]]] = {}
    for table in sorted(rows):
        ordered = sorted(rows[table], key=_masked)
        normalized[table] = [
            {
                name: (
                    TIMESTAMP_PLACEHOLDER
                    if classify(value) == "timestamp"
                    else MINTED_ID.sub(rename, value)
                    if classify(value) == "minted"
                    else value
                )
                for name, value in sorted(row.items())
            }
            for row in ordered
        ]
    return normalized
