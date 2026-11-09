"""A hub of linked projects the project-link and landing-project reads share: projects ``p0``-``p3`` over sources
``s0``-``s3`` and repositories ``r0``-``r3``."""

from __future__ import annotations

from pathlib import Path

from blizzard.hub.domain.config.repositories import RepositoryId, RepositorySlug
from blizzard.hub.domain.projects.model import ProjectId, ProjectSlug
from tests.support.scaffolding import HubHarness, build_hub

N = 4

#: The forge every source and repository here is on.
GITHUB = {"slug": "github", "kind": "github", "api_base": "https://api.github.com"}


def source_doc(slug: str) -> dict[str, object]:
    return {
        "slug": slug,
        "provider": "github",
        "locator": f"acme/{slug}",
        "forge": "github",
        "secret": "gh",
        "annotate": False,
    }


def repository_doc(slug: str) -> dict[str, object]:
    return {
        "slug": slug,
        "forge": "github",
        "owner": "acme",
        "repo": slug,
        "base_branch": "master",
        "secret_name": "gh",
    }


def apply_config(hub: HubHarness, document: dict[str, object]) -> list[str]:
    applied = hub.client.post("/api/config/apply", json={"version": 2, "forges": [GITHUB], **document})
    assert applied.status_code == 200, applied.text
    return [outcome["op"] for outcome in applied.json()["outcomes"]]


def project_id(hub: HubHarness, slug: str) -> ProjectId:
    project = hub.services.projects.get_by_slug(ProjectSlug.parse(slug))
    assert project is not None
    return project.project_id


def repository_id(hub: HubHarness, slug: str) -> RepositoryId:
    record = hub.services.repository_records.get_by_slug(RepositorySlug.parse(slug))
    assert record is not None
    return record.repository_id


def build_links_hub(tmp_path: Path) -> HubHarness:
    """Projects ``p0``-``p3`` over sources ``s0``-``s3`` and repositories ``r0``-``r3``: ``p{i}`` links ``s0``..``s{i}``
    and ``r0``..``r{i}``, then ``p3`` unlinks ``s0`` and ``r0`` — a stored link in its unlinked state."""
    hub = build_hub(tmp_path, repositories=(), link=False)
    assert hub.client.post("/api/secrets", json={"name": "gh", "value": "v"}).status_code == 201
    sources = [f"s{i}" for i in range(N)]
    repositories = [f"r{i}" for i in range(N)]
    projects = [
        {"slug": f"p{i}", "name": f"P{i}", "sources": sources[: i + 1], "repositories": repositories[: i + 1]}
        for i in range(N)
    ]
    apply_config(
        hub,
        {
            "work_sources": [source_doc(s) for s in sources],
            "repositories": [repository_doc(r) for r in repositories],
            "projects": projects,
        },
    )
    apply_config(hub, {"projects": [{"slug": "p3", "sources": sources[1:], "repositories": repositories[1:]}]})
    return hub
