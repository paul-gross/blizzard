"""The refusal a landed marker naming an ambiguous repository answers with."""

from __future__ import annotations

from collections.abc import Mapping


class AmbiguousMarkerRepository(Exception):
    """A landed marker names an ``owner/repo`` the visit holds for two different repository records —
    the marker name cannot say which one landed. ``records`` are their ids; the message names their slugs."""

    def __init__(self, repo: str, held: Mapping[str, str]) -> None:
        self.records = tuple(sorted(held))
        slugs = sorted(held.values())
        super().__init__(
            f"{repo} resolves to repository records {' and '.join(slugs)}; the marker cannot say which landed"
        )
