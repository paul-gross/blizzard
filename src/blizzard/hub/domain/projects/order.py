"""A project's landing order: the place of each repository it links in the order a chunk's repositories land in.

A place is an append-only fact on a link, the newest winning (``bzh:facts-not-status``); a link with no fact, or whose
newest fact is null, is unranked. A position is a float, so a move writes one fact — the midpoint between its new
neighbours — after renumbering the ranked links ``1.0``, ``2.0``, … when that gap is spent; a whole-order write
ranks the links it names ``1.0``, ``2.0``, … in its order and leaves every other link unranked. Either writes a fact
only for a link whose position changes, so writing an order the project already has writes nothing. A position write
is not a revision of the link: it carries who set it, and writes no change row."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.landing_policies import landing_sort_key
from blizzard.hub.domain.config.repositories import RepositoryId
from blizzard.hub.domain.kernel.float_slots import land_after
from blizzard.hub.domain.projects.model import ProjectId

#: Where the first ranked repository of a project sits, and where a renumbering starts.
FIRST_POSITION = 1.0


class RepositoryNotLinked(ConfigFieldError):
    """A position names a repository the project does not link."""

    code = "repository_not_linked"

    def __init__(self, field: str, repository: str, project: str) -> None:
        super().__init__(field, f"project {project} doesn't link repository {repository}")


@domain_model
@dataclass(frozen=True)
class RankedLink:
    """One standing repository link of a project: the repository, its current slug, and its position — ``None`` when
    unranked."""

    repository_id: RepositoryId
    slug: str
    position: float | None


@domain_model
@dataclass(frozen=True)
class PositionFact:
    """One position to write for a link of the project; ``None`` makes it unranked."""

    repository_id: RepositoryId
    position: float | None


def in_landing_order(links: Sequence[RankedLink]) -> list[RankedLink]:
    """``links`` in the order their repositories land in."""
    return sorted(links, key=lambda link: landing_sort_key(link.position, link.slug))


def whole_order(
    links: Sequence[RankedLink], ordered: Sequence[RepositoryId], *, project: str, field: str = "repositories"
) -> tuple[PositionFact, ...]:
    """The facts that rank ``ordered`` ``1.0``, ``2.0``, … and every other of ``links`` unranked.
    :class:`RepositoryNotLinked` for a repository ``links`` does not hold; :class:`ConfigFieldError` for one named
    twice — each on ``field``, the list that named it."""
    by_id = {link.repository_id: link for link in links}
    seen: set[RepositoryId] = set()
    for repository_id in ordered:
        if repository_id not in by_id:
            raise RepositoryNotLinked(field, repository_id, project)
        if repository_id in seen:
            raise ConfigFieldError(field, f"repository {by_id[repository_id].slug} is named twice")
        seen.add(repository_id)
    wanted: dict[RepositoryId, float | None] = {link.repository_id: None for link in links}
    wanted.update((repository_id, FIRST_POSITION + index) for index, repository_id in enumerate(ordered))
    return _changed(by_id, wanted.items())


def move(
    links: Sequence[RankedLink],
    repository_id: RepositoryId,
    *,
    after: RepositoryId | None,
    unranked: bool,
    project: str,
) -> tuple[PositionFact, ...]:
    """The facts that move ``repository_id`` to unranked, or to just after the ranked ``after`` — to the front when
    ``after`` is ``None``. :class:`RepositoryNotLinked` for a repository ``links`` does not hold;
    :class:`ConfigFieldError` for a move both unranked and after a repository, after itself, or after an unranked
    one."""
    by_id = {link.repository_id: link for link in links}
    if repository_id not in by_id:
        raise RepositoryNotLinked("repository", repository_id, project)
    if after is not None and after not in by_id:
        raise RepositoryNotLinked("after_repository", after, project)
    if unranked:
        if after is not None:
            raise ConfigFieldError("after_repository", "an unranked repository is after no other")
        return _changed(by_id, [(repository_id, None)])
    if after == repository_id:
        raise ConfigFieldError("after_repository", "a repository cannot move after itself")
    ranked = [
        link for link in in_landing_order(links) if link.position is not None and link.repository_id != repository_id
    ]
    index = None
    if after is not None:
        index = next((i for i, link in enumerate(ranked) if link.repository_id == after), None)
        if index is None:
            raise ConfigFieldError("after_repository", f"repository {by_id[after].slug} is unranked; rank it first")
    slot = land_after([link.position for link in ranked if link.position is not None], index, start=FIRST_POSITION)
    wanted = [(link.repository_id, position) for link, position in zip(ranked, slot.renumbered, strict=False)]
    return _changed(by_id, [*wanted, (repository_id, slot.position)])


def _changed(
    by_id: Mapping[RepositoryId, RankedLink], wanted: Iterable[tuple[RepositoryId, float | None]]
) -> tuple[PositionFact, ...]:
    """A fact for each of ``wanted`` whose link does not already stand there."""
    return tuple(
        PositionFact(repository_id, position)
        for repository_id, position in wanted
        if by_id[repository_id].position != position
    )


class IReadProjectPositions(Protocol):
    """Read-only landing-order operations, within one tenant."""

    def landing_links(self, project_id: ProjectId) -> list[RankedLink]:
        """The project's standing repository links, each with its repository's current slug and its newest
        position, in landing order. A link unlinked and linked again has the position it had."""
        ...


class IWriteProjectPositions(IReadProjectPositions, Protocol):
    """Adds the position write."""

    def record_positions(self, project_id: ProjectId, facts: Sequence[PositionFact], *, at: datetime, by: str) -> None:
        """Append one position fact per entry of ``facts``, in order, in one transaction."""
        ...
