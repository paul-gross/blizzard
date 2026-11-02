"""The landed-commit link binding: a landed repo's commit address on its repository record's forge."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.chunk.ports.commit_links import CommitLink, no_commit_link
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeId, IReadForgeRepository
from blizzard.hub.domain.config.repositories import (
    CommitOrigin,
    ConfiguredRepository,
    IReadRepositoryRecordRepository,
    RepositoryId,
    resolve_origin_record,
)
from blizzard.hub.domain.projects.links import IReadProjectLinks
from blizzard.hub.forge.web import web_links


@dataclass(frozen=True)
class LandedCommitLinks:
    """Resolves a landed name by delivery's rules — among the records the chunk's project links, standing at the
    chunk's mint time — and asks that record's forge kind for the commit address. A chunk with no project, and a
    name resolving to no one record, get ``None``."""

    project_links: IReadProjectLinks
    repository_records: IReadRepositoryRecordRepository
    forge_records: IReadForgeRepository

    def for_chunks(self, chunks: Sequence[Chunk]) -> dict[str, CommitLink]:
        projects = sorted({c.project_id for c in chunks if c.project_id is not None})
        links = self.project_links.repositories_of_projects(projects) if projects else {}
        linked_ids = sorted({link.repository_id for rows in links.values() for link in rows})
        records = list(self.repository_records.get_many(linked_ids).values()) if linked_ids else []
        forge_ids = list({record.fields.forge_id for record in records})
        forges = self.forge_records.get_many(forge_ids) if forge_ids else {}
        result: dict[str, CommitLink] = {}
        for chunk in chunks:
            if chunk.project_id is None or not links.get(chunk.project_id):
                result[chunk.chunk_id] = no_commit_link
                continue
            linked = frozenset(link.repository_id for link in links[chunk.project_id])
            result[chunk.chunk_id] = _lookup(chunk, linked, records, forges)
        return result


def _lookup(
    chunk: Chunk,
    linked: frozenset[RepositoryId],
    records: list[ConfiguredRepository],
    forges: dict[ForgeId, ConfiguredForge],
) -> CommitLink:
    def link(repo: str, sha: str) -> str | None:
        record = resolve_origin_record(
            CommitOrigin.of(None, repo), records, forges.values(), minted_at=chunk.minted_at, linked=linked
        )
        if record is None:
            return None
        links = web_links(forges[record.fields.forge_id])
        return links.commit_url(record.fields, sha) if links is not None else None

    return link
