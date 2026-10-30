"""Resolve current delivery declarations for a page of chunks."""

from collections.abc import Sequence

from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.actions.visit import GitCommits, UnconvergedDeliveryError
from blizzard.hub.domain.chunk.delivery_read import DeliverySources
from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.config.repositories import RepositoryOutcome, resolve_chunk_repositories


def delivery_declarations_for(
    services: TenantServices, chunks: Sequence[Chunk], sources: dict[str, DeliverySources]
) -> dict[str, list[dict[str, str | None]]]:
    relevant = [
        chunk
        for chunk in chunks
        if (source := sources.get(chunk.chunk_id)) is not None
        and source.commits
        and any(row.name.startswith("no-code/") for row in source.markers)
    ]
    if not relevant:
        return {}
    links = services.project_links.repositories_of_projects(
        sorted({c.project_id for c in relevant if c.project_id is not None})
    )
    linked_ids = {link.repository_id for rows in links.values() for link in rows}
    records = list(services.repository_records.get_many(sorted(linked_ids)).values()) if linked_ids else []
    forges = services.forge_records.get_many(
        list({record.fields.forge_id for record in records if record.repository_id in linked_ids})
    )
    result: dict[str, list[dict[str, str | None]]] = {}
    for chunk in relevant:
        try:
            commits = GitCommits.of(sources[chunk.chunk_id].commits)
        except UnconvergedDeliveryError:
            continue
        linked = (
            frozenset(link.repository_id for link in links.get(chunk.project_id, []))
            if chunk.project_id
            else frozenset()
        )
        resolution = resolve_chunk_repositories(
            commits.origins(), records, forges.values(), minted_at=chunk.minted_at, linked=linked
        )
        if resolution.outcome is RepositoryOutcome.RESOLVED:
            result[chunk.chunk_id] = commits.payload(resolution.qualified, resolution)
    return result
