"""The hub's config and project-link seams' set-keyed reads (component tier) — the
``IReadProjectLinks`` plurals, ``ISecretCatalog.retired_names``' bound,
``IReadConfigRevisions.revisions_for`` and ``IReadGraphRepository.enabled_names``, each the
batched sibling of a singular (`bzh:bulk-reconstitution`), and the loops that call them.

Per plural: it matches its singular per key and drops an unknown key, issues no statement for
no keys, stays correct across a lowered ``BATCH_SIZE``, and its statement count is equal at
one key and four. Per call site — config apply and export, ``choose_project``, ``resolve``,
the graph mint's cross-graph check — the statement count is equal at one and four
projects, sources or graph names."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.store import batching as batching_module
from blizzard.hub.api.fleet import _flow_namer as flow_namer  # pyright: ignore[reportPrivateUsage]
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.chunk.ports.work_refs import work_ref_lock
from blizzard.hub.domain.config.changes import RecordKind
from blizzard.hub.domain.config.repositories import RepositoryId
from blizzard.hub.domain.config.work_sources import WorkSourceId, WorkSourceSlug
from blizzard.hub.domain.delivery.flows import Flow
from blizzard.hub.domain.graph.model import GraphDoc
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.live_config import ConfigObjectCache
from blizzard.hub.store.internal.config_revisions_store import ConfigRevisionsStore
from blizzard.hub.work_sources.internal.hub_work_source import HubWorkSource
from blizzard.hub.work_sources.registry import BuiltWorkSource, StoreWorkSourceRegistry
from tests.support.chunk_units import chunk_unit
from tests.support.linked_projects_world import (
    N,
    apply_config,
    build_links_hub,
    project_id,
    repository_doc,
    repository_id,
    source_doc,
)
from tests.support.scaffolding import (
    FakeWorkSource,
    HubHarness,
    build_hub,
    capture_statements,
    count_queries,
    hub_store_connections,
)

_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _graph_yaml(name: str) -> str:
    return f"""
name: {name}
entry: build
nodes:
  build:
    executor: runner
    prompt: do the work
    judgement:
      prompt: judge it
      choices:
        pass:
          description: it works
          to: done
"""


def _config_hub(tmp_path: Path, n: int) -> HubHarness:
    """A hub holding ``n`` projects ``p{i}``, each linked to source ``s{i}`` and repository ``r{i}``, and ``n``
    routines of the default project, each naming its own enabled graph ``g{i}``."""
    hub = build_hub(tmp_path, repositories=(), link=False)
    assert hub.client.post("/api/secrets", json={"name": "gh", "value": "v"}).status_code == 201
    for i in range(n):
        assert hub.client.post("/api/graphs", json={"definition_yaml": _graph_yaml(f"g{i}")}).status_code == 201
    apply_config(
        hub,
        {
            "work_sources": [source_doc(f"s{i}") for i in range(n)],
            "repositories": [repository_doc(f"r{i}") for i in range(n)],
            "projects": [
                {"slug": f"p{i}", "name": f"P{i}", "sources": [f"s{i}"], "repositories": [f"r{i}"]} for i in range(n)
            ],
            "scopes": [{"slug": "core", "project": "default"}],
            "routines": [
                {"name": f"nightly-{i}", "graph_name": f"g{i}", "default_scope_slug": "core", "project": "default"}
                for i in range(n)
            ],
        },
    )
    return hub


def _source_id(hub: HubHarness, slug: str) -> WorkSourceId:
    record = hub.services.work_source_records.get_by_slug(WorkSourceSlug.parse(slug))
    assert record is not None
    return record.work_source_id


@pytest.fixture
def links_hub(tmp_path: Path) -> HubHarness:
    """See :func:`build_links_hub`."""
    return build_links_hub(tmp_path)


def _flat(hub: HubHarness, read: Callable[[int], object]) -> None:
    assert count_queries(hub.engine, lambda: read(1)) == count_queries(hub.engine, lambda: read(N))


# --- IReadProjectLinks ---------------------------------------------------------------------


def _pairs(hub: HubHarness, kind: str) -> list[tuple[ProjectId, str]]:
    """Every (project, record) pair of the fixture — standing, unlinked, and never linked."""
    of = _source_id if kind == "s" else repository_id
    return [(project_id(hub, f"p{p}"), of(hub, f"{kind}{r}")) for p in range(N) for r in range(N)]


def test_source_links_for_matches_source_link_and_drops_a_never_linked_pair(links_hub: HubHarness) -> None:
    links = links_hub.services.project_links
    pairs = [(p, WorkSourceId(s)) for p, s in _pairs(links_hub, "s")]
    unknown = (project_id(links_hub, "p0"), WorkSourceId("wsrc_never"))

    found = links.source_links_for([*pairs, unknown])

    assert found == {pair: link for pair in pairs if (link := links.source_link(*pair)) is not None}
    assert unknown not in found
    assert not found[(project_id(links_hub, "p3"), _source_id(links_hub, "s0"))].linked
    assert len(found) == sum(range(1, N + 1))


def test_repository_links_for_matches_repository_link_and_drops_a_never_linked_pair(links_hub: HubHarness) -> None:
    links = links_hub.services.project_links
    pairs = [(p, RepositoryId(r)) for p, r in _pairs(links_hub, "r")]
    unknown = (ProjectId("prj_never"), repository_id(links_hub, "r0"))

    found = links.repository_links_for([*pairs, unknown])

    assert found == {pair: link for pair in pairs if (link := links.repository_link(*pair)) is not None}
    assert unknown not in found
    assert not found[(project_id(links_hub, "p3"), repository_id(links_hub, "r0"))].linked


def test_the_of_projects_plurals_match_their_singulars_and_drop_an_unknown_project(links_hub: HubHarness) -> None:
    links = links_hub.services.project_links
    projects = [project_id(links_hub, f"p{i}") for i in range(N)]

    sources = links.sources_of_projects([*projects, ProjectId("prj_never")])
    repositories = links.repositories_of_projects([*projects, ProjectId("prj_never")])

    assert sources == {p: links.sources_of(p) for p in projects}
    assert repositories == {p: links.repositories_of(p) for p in projects}
    assert [link.work_source_id for link in sources[projects[3]]] == sorted(
        _source_id(links_hub, f"s{i}") for i in range(1, N)
    )


def test_projects_linking_sources_matches_the_singular_and_drops_an_unlinked_source(links_hub: HubHarness) -> None:
    links = links_hub.services.project_links
    sources = [_source_id(links_hub, f"s{i}") for i in range(N)]

    found = links.projects_linking_sources([*sources, "wsrc_never"])

    assert found == {s: links.projects_linking_source(s) for s in sources}
    assert project_id(links_hub, "p3") not in found[sources[0]]
    assert "wsrc_never" not in found


def test_the_link_plurals_match_across_a_batch_boundary(links_hub: HubHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    links = links_hub.services.project_links
    projects = [project_id(links_hub, f"p{i}") for i in range(N)]
    sources = [_source_id(links_hub, f"s{i}") for i in range(N)]
    source_pairs = [(p, WorkSourceId(s)) for p, s in _pairs(links_hub, "s")]
    repository_pairs = [(p, RepositoryId(r)) for p, r in _pairs(links_hub, "r")]
    unbatched = (
        links.source_links_for(source_pairs),
        links.repository_links_for(repository_pairs),
        links.sources_of_projects(projects),
        links.repositories_of_projects(projects),
        links.projects_linking_sources(sources),
    )

    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)

    assert (
        links.source_links_for(source_pairs),
        links.repository_links_for(repository_pairs),
        links.sources_of_projects(projects),
        links.repositories_of_projects(projects),
        links.projects_linking_sources(sources),
    ) == unbatched


def test_the_link_plurals_issue_no_statement_for_no_keys(links_hub: HubHarness) -> None:
    links = links_hub.services.project_links
    for read in (
        links.source_links_for,
        links.repository_links_for,
        links.sources_of_projects,
        links.repositories_of_projects,
        links.projects_linking_sources,
    ):
        assert count_queries(links_hub.engine, lambda read=read: read([])) == 0, read


def test_the_link_plurals_statement_counts_are_flat_across_key_set_sizes(links_hub: HubHarness) -> None:
    links = links_hub.services.project_links
    projects = [project_id(links_hub, f"p{i}") for i in range(N)]
    sources = [_source_id(links_hub, f"s{i}") for i in range(N)]
    repositories = [repository_id(links_hub, f"r{i}") for i in range(N)]
    _flat(links_hub, lambda n: links.source_links_for([(projects[i], sources[i]) for i in range(n)]))
    _flat(links_hub, lambda n: links.repository_links_for([(projects[i], repositories[i]) for i in range(n)]))
    _flat(links_hub, lambda n: links.sources_of_projects(projects[:n]))
    _flat(links_hub, lambda n: links.repositories_of_projects(projects[:n]))
    _flat(links_hub, lambda n: links.projects_linking_sources(sources[:n]))


# --- ISecretCatalog.retired_names ----------------------------------------------------------


@pytest.fixture
def secrets_hub(tmp_path: Path) -> HubHarness:
    """Secrets ``k0``-``k3``: ``k1`` retired, ``k2`` retired and re-enabled, ``k3`` retired twice over."""
    hub = build_hub(tmp_path, repositories=(), link=False)
    for i in range(N):
        assert hub.client.post("/api/secrets", json={"name": f"k{i}", "value": "v"}).status_code == 201
    for name, verbs in {"k1": ["retire"], "k2": ["retire", "enable"], "k3": ["retire", "enable", "retire"]}.items():
        for verb in verbs:
            assert hub.client.post(f"/api/secrets/{name}/{verb}").status_code == 200
    return hub


def test_bounded_retired_names_agrees_with_is_retired_and_drops_an_unknown_name(secrets_hub: HubHarness) -> None:
    catalog = secrets_hub.services.secret_catalog
    names = [f"k{i}" for i in range(N)]

    assert catalog.retired_names([*names, "never"]) == {"k1", "k3"}
    assert catalog.retired_names(names) == {name for name in names if catalog.is_retired(name)}
    assert catalog.retired_names() == {"k1", "k3"}


def test_bounded_retired_names_matches_across_a_batch_boundary(
    secrets_hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    assert secrets_hub.services.secret_catalog.retired_names([f"k{i}" for i in range(N)]) == {"k1", "k3"}


def test_bounded_retired_names_is_statement_free_when_empty_and_flat_otherwise(secrets_hub: HubHarness) -> None:
    catalog = secrets_hub.services.secret_catalog
    assert count_queries(secrets_hub.engine, lambda: catalog.retired_names([])) == 0
    _flat(secrets_hub, lambda n: catalog.retired_names([f"k{i}" for i in range(n)]))


# --- IReadConfigRevisions.revisions_for ----------------------------------------------------


@pytest.mark.parametrize("kind", [RecordKind.WORK_SOURCE, RecordKind.REPOSITORY, RecordKind.SECRET])
def test_revisions_for_matches_revisions_and_drops_an_unknown_key(
    links_hub: HubHarness, kind: RecordKind, monkeypatch: pytest.MonkeyPatch
) -> None:
    revisions = ConfigRevisionsStore(hub_store_connections(links_hub.engine))
    keys = {
        RecordKind.WORK_SOURCE: [_source_id(links_hub, f"s{i}") for i in range(N)],
        RecordKind.REPOSITORY: [repository_id(links_hub, f"r{i}") for i in range(N)],
        RecordKind.SECRET: ["gh"],
    }[kind]

    found = revisions.revisions_for(kind, [*keys, "never"])

    assert found == {key: revisions.revisions(kind, key) for key in keys}
    assert count_queries(links_hub.engine, lambda: revisions.revisions_for(kind, [])) == 0
    _flat(links_hub, lambda n: revisions.revisions_for(kind, keys[:n]))
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    assert revisions.revisions_for(kind, keys) == found


# --- IReadGraphRepository.enabled_names ----------------------------------------------------


@pytest.fixture
def graphs_hub(tmp_path: Path) -> HubHarness:
    """Graphs ``g0``-``g3``: ``g1``'s one mint retired; ``g2`` minted twice, its newest retired."""
    hub = build_hub(tmp_path, repositories=(), link=False)
    minted: dict[str, list[str]] = {}
    for i in range(N):
        made = hub.client.post("/api/graphs", json={"definition_yaml": _graph_yaml(f"g{i}")})
        assert made.status_code == 201, made.text
        minted.setdefault(f"g{i}", []).append(made.json()["graph_id"])
    again = hub.client.post("/api/graphs", json={"definition_yaml": _graph_yaml("g2").replace("do the", "redo the")})
    assert again.status_code == 201, again.text
    for graph_id in (minted["g1"][0], again.json()["graph_id"]):
        assert hub.client.post(f"/api/graphs/{graph_id}/retire").status_code == 202
    return hub


def test_enabled_names_agrees_with_get_enabled_by_name_and_drops_an_unknown_name(graphs_hub: HubHarness) -> None:
    graphs = graphs_hub.services.graphs
    names = [f"g{i}" for i in range(N)]

    found = graphs.enabled_names([*names, "never"])

    assert found == {name for name in names if graphs.get_enabled_by_name(name) is not None}
    assert found == {"g0", "g2", "g3"}


def test_enabled_names_matches_across_a_batch_boundary(graphs_hub: HubHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    assert graphs_hub.services.graphs.enabled_names([f"g{i}" for i in range(N)]) == {"g0", "g2", "g3"}


def test_enabled_names_is_statement_free_when_empty_and_flat_otherwise(graphs_hub: HubHarness) -> None:
    graphs = graphs_hub.services.graphs
    assert count_queries(graphs_hub.engine, lambda: graphs.enabled_names([])) == 0
    _flat(graphs_hub, lambda n: graphs.enabled_names([f"g{i}" for i in range(n)]))


# --- IReadGraphRepository.get_enabled_by_names ---------------------------------------------


def test_get_enabled_by_names_agrees_with_the_singular_and_drops_missing_and_retired_names(
    graphs_hub: HubHarness,
) -> None:
    graphs = graphs_hub.services.graphs
    names = [f"g{i}" for i in range(N)]

    found = graphs.get_enabled_by_names([*names, "never"])

    assert set(found) == {"g0", "g2", "g3"}  # g1 is all-retired; g2's enabled mint is its older one
    for name, graph in found.items():
        assert graph == graphs.get_enabled_by_name(name)


def test_get_enabled_by_names_matches_across_a_batch_boundary(
    graphs_hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 2)
    found = graphs_hub.services.graphs.get_enabled_by_names([f"g{i}" for i in range(N)])
    assert set(found) == {"g0", "g2", "g3"}


def test_get_enabled_by_names_is_statement_free_when_empty_and_flat_otherwise(graphs_hub: HubHarness) -> None:
    graphs = graphs_hub.services.graphs
    assert count_queries(graphs_hub.engine, lambda: graphs.get_enabled_by_names([])) == 0
    _flat(graphs_hub, lambda n: graphs.get_enabled_by_names([f"g{i}" for i in range(n)]))


# --- _flow_namer ---------------------------------------------------------------------------


def _flow(repository_id: str, branch: str = "feat") -> Flow:
    return Flow(flow_id=f"fl_{repository_id}", repository_id=repository_id, branch=branch, created_at=_AT)


def test_the_flow_namer_names_flows_from_one_preload_and_falls_back_to_the_raw_id(links_hub: HubHarness) -> None:
    flows = [_flow(repository_id(links_hub, f"r{i}")) for i in range(N)]
    flows.append(_flow("rp_gone"))

    namer = flow_namer(links_hub.services, flows)

    assert [namer(f) for f in flows] == [*(f"r{i}/feat" for i in range(N)), "rp_gone/feat"]


def test_the_flow_namer_statements_are_flat_across_flow_counts(links_hub: HubHarness) -> None:
    flows = [_flow(repository_id(links_hub, f"r{i}")) for i in range(N)]

    def build(n: int) -> object:
        namer = flow_namer(links_hub.services, flows[:n])
        return [namer(f) for f in flows[:n]]

    assert count_queries(links_hub.engine, lambda: build(1)) == count_queries(links_hub.engine, lambda: build(N))
    assert count_queries(links_hub.engine, lambda: flow_namer(links_hub.services, [])) == 0


# --- Call sites ----------------------------------------------------------------------------


def _apply_statements(hub: HubHarness, document: dict[str, object]) -> tuple[list[str], int]:
    """The ops a no-op apply of ``document`` answers, and the statements it issues."""
    ops: list[str] = []
    with capture_statements(hub.engine) as statements:
        ops.extend(apply_config(hub, document))
    return ops, len(statements)


def test_config_export_and_a_no_op_apply_are_flat_across_project_counts(tmp_path: Path) -> None:
    counts: list[tuple[int, int]] = []
    for n in (1, N):
        (tmp_path / str(n)).mkdir()
        hub = _config_hub(tmp_path / str(n), n)
        exported = hub.client.get("/api/config/export").json()
        assert len(exported["projects"]) == n + 1
        export = count_queries(hub.engine, lambda hub=hub: hub.client.get("/api/config/export"))
        ops, apply = _apply_statements(hub, exported)
        assert set(ops) == {"unchanged"}
        counts.append((export, apply))
    assert counts[0] == counts[1]


def test_choose_project_is_flat_across_source_counts(tmp_path: Path) -> None:
    names = [f"src{i}" for i in range(N)]
    hub = build_hub(tmp_path, work_sources={name: FakeWorkSource(name=name) for name in names})
    ingest = hub.services.ingest

    def choose(n: int) -> None:
        assert ingest.choose_project([WorkRef(source=name, ref="1") for name in names[:n]], None)

    _flat(hub, choose)


def test_resolve_reads_every_active_source_s_revisions_once(tmp_path: Path) -> None:
    counts: list[int] = []
    for n in (1, N):
        names = [f"src{i}" for i in range(n)]
        (tmp_path / str(n)).mkdir()
        hub = build_hub(tmp_path / str(n), work_sources={name: FakeWorkSource(name=name) for name in names})
        built_in = hub.work_sources.get("hub")
        assert isinstance(built_in, HubWorkSource)
        registry = StoreWorkSourceRegistry(
            records=hub.services.work_source_records,
            objects=ConfigObjectCache(ConfigRevisionsStore(hub_store_connections(hub.engine))),
            build=lambda record: BuiltWorkSource(record, FakeWorkSource(name=record.name), lambda: None),
            built_in=built_in,
        )
        registry.resolve("unclaimed")  # Builds every source, so the measured call only compares revisions.
        assert registry.resolve(f"{names[-1]}#7") == WorkRef(source=names[-1], ref="7")
        counts.append(count_queries(hub.engine, lambda registry=registry: registry.resolve("unclaimed")))
    assert counts[0] == counts[1]


def test_the_graph_mint_s_cross_graph_check_is_flat_across_target_counts(graphs_hub: HubHarness) -> None:
    mint = graphs_hub.services.graph_mint

    def doc(n: int) -> GraphDoc:
        choices = {f"to-g{i}": {"description": f"to g{i}", "to": f"graph:g{i}"} for i in range(n)}
        return GraphDoc.of(
            {
                "name": "src",
                "entry": "build",
                "nodes": {"build": {"executor": "runner", "judgement": {"prompt": "p", "choices": choices}}},
            }
        )

    _flat(graphs_hub, lambda n: mint.prepared(doc(n)))
    _, warnings = mint.prepared(doc(N))
    assert [w for w in warnings if "g1" in w] and not [w for w in warnings if "g0" in w]


def test_the_transaction_taking_source_links_answer_every_source_flat_across_source_counts(
    links_hub: HubHarness,
) -> None:
    sources = [_source_id(links_hub, f"s{i}") for i in range(N)]
    links = links_hub.services.project_links

    with chunk_unit(
        hub_store_connections(links_hub.engine), [], keys=[work_ref_lock(WorkRef(source=s, ref="1")) for s in sources]
    ) as tx:
        found = links.projects_linking_sources([*sources, "wsrc_never"], transaction=tx)
        _flat(links_hub, lambda n: links.projects_linking_sources(sources[:n], transaction=tx))

    assert found == {s: linking for s in sources if (linking := links.projects_linking_source(s))}
