"""Packaged-graph reconciliation — mint only what changed (issue #146).

Graphs live in the store, not on disk, so shipping a changed graph in a new wheel used to
change nothing. Drives the real reconciler over a fixture "packaged set" — mint iff the
inlined definition differs. One case uses the real set: reconciling the wheel twice is a no-op.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from blizzard.hub.domain.graph import GraphArtifact
from blizzard.hub.graph_sync import GraphReconciliation, GraphSyncStatus
from blizzard.hub.graphs import PACKAGED
from tests.support import HubHarness, build_hub

pytestmark = pytest.mark.component

_GRAPH_YAML = """
name: {name}
entry: build
nodes:
  build:
    executor: runner
    prompt: ./prompts/build.md
    judgement:
      prompt: judge it
      choices:
        pass:
          description: it works
          to: done
    retries:
      max: 1
      exhausted: escalate
"""


def _packaged(tmp_path: Path, name: str, *, prompt: str = "do the work", body: str | None = None) -> Path:
    """Write one packaged-graph directory — ``graph.yaml`` plus its own ``prompts/``."""
    directory = tmp_path / "graphs" / name
    (directory / "prompts").mkdir(parents=True, exist_ok=True)
    (directory / "prompts" / "build.md").write_text(prompt)
    graph_yaml = directory / "graph.yaml"
    graph_yaml.write_text(body if body is not None else _GRAPH_YAML.format(name=name))
    return graph_yaml


def _sync(hub: HubHarness, paths: list[Path]):  # type: ignore[no-untyped-def]
    return GraphReconciliation(hub.services.graph_mint, hub.services.graphs, paths).outcomes()


def _statuses(outcomes) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    return [(o.name, o.status.value) for o in outcomes]


def test_a_graph_with_no_lineage_mints_as_the_first_of_its_name(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    path = _packaged(tmp_path, "fresh")

    outcomes = _sync(hub, [path])

    assert _statuses(outcomes) == [("fresh", "minted")]
    assert outcomes[0].graph_id is not None
    assert outcomes[0].detail == "first of its name"
    listed = hub.client.get("/api/graphs").json()
    assert [(g["name"], g["effective"]) for g in listed] == [("fresh", True)]


def test_reconciling_twice_mints_nothing_the_second_time(tmp_path: Path) -> None:
    # Idempotence is what makes this safe to run at the end of *every* deploy: a wheel
    # that changed no graph must not supersede a definition or churn its lineage.
    hub = build_hub(tmp_path)
    path = _packaged(tmp_path, "steady")

    first = _sync(hub, [path])
    second = _sync(hub, [path])

    assert _statuses(first) == [("steady", "minted")]
    assert _statuses(second) == [("steady", "up-to-date")]
    assert second[0].graph_id is None
    assert len(hub.client.get("/api/graphs").json()) == 1  # one mint, not two


def test_a_prompt_only_edit_is_detected_and_minted(tmp_path: Path) -> None:
    # The case a `graph.yaml` diff misses: `mint` inlines every prompt file reference, so
    # editing `prompts/build.md` changes the minted graph while `graph.yaml` stays identical.
    hub = build_hub(tmp_path)
    path = _packaged(tmp_path, "prompted", prompt="do the work")
    first = _sync(hub, [path])[0]
    before = path.read_text()

    hub.clock.advance(timedelta(minutes=1))  # distinct created_at → a deterministic newest
    (path.parent / "prompts" / "build.md").write_text("do the work, but carefully")
    outcomes = _sync(hub, [path])

    assert path.read_text() == before  # graph.yaml untouched
    assert _statuses(outcomes) == [("prompted", "minted")]
    assert outcomes[0].detail == "packaged definition differs from the newest mint"
    # The new mint is effective and supersedes the prior one; both are retained.
    listed = {g["graph_id"]: g for g in hub.client.get("/api/graphs").json()}
    assert listed[outcomes[0].graph_id]["effective"] is True
    assert listed[first.graph_id]["effective"] is False
    shown = hub.client.get(f"/api/graphs/{outcomes[0].graph_id}").json()
    assert shown["nodes"][0]["prompt"] == "do the work, but carefully"


def test_reformatting_the_yaml_is_not_a_change(tmp_path: Path) -> None:
    # The comparison is over the parsed, inlined definition — not bytes — so quoting,
    # block style and key order do not churn the lineage.
    hub = build_hub(tmp_path)
    path = _packaged(tmp_path, "tidy")
    _sync(hub, [path])
    reformatted = yaml.safe_dump(yaml.safe_load(path.read_text()), sort_keys=True, default_flow_style=True)
    path.write_text(reformatted)

    outcomes = _sync(hub, [path])

    assert _statuses(outcomes) == [("tidy", "up-to-date")]


# One declared artifact file, its entry name deliberately unlike its filename: an error
# message carrying only one of the two can then never satisfy an assertion for the other.
_ARTIFACT_GRAPH_YAML = """
name: {name}
artifacts:
  docket: ./reference-notes.md
entry: build
nodes:
  build:
    executor: runner
    prompt: ./prompts/build.md
    judgement:
      prompt: judge it
      choices:
        pass:
          description: it works
          to: done
"""

# Multi-line, blank-line-bearing, newline-terminated — the shape a real declared file has,
# which the mint must bake byte-identically rather than reflow.
_DOCKET_TEXT = "# Docket\n\n- one term: what it means\n- another term\n\n  a continuation line\n"


def _docketed(tmp_path: Path, name: str, *, docket: str | None = _DOCKET_TEXT) -> Path:
    """One packaged-graph directory declaring `artifacts:`; ``docket=None`` leaves the
    declared file absent."""
    graph_yaml = _packaged(tmp_path, name, body=_ARTIFACT_GRAPH_YAML.format(name=name))
    if docket is not None:
        (graph_yaml.parent / "reference-notes.md").write_text(docket)
    return graph_yaml


def test_a_declared_artifact_file_mints_with_its_own_text_baked(tmp_path: Path) -> None:
    # The file-minted half of the declaration: reconciliation inlines `artifacts:` at load
    # and bakes each entry's file text into the mint verbatim, multi-line content included.
    hub = build_hub(tmp_path)
    graph_yaml = _docketed(tmp_path, "docketed")

    outcomes = _sync(hub, [graph_yaml])

    assert _statuses(outcomes) == [("docketed", "minted")]
    graph_id = outcomes[0].graph_id
    assert graph_id is not None
    minted = hub.services.graphs.get(graph_id)
    assert minted is not None
    assert minted.artifacts == [GraphArtifact(name="docket", content=_DOCKET_TEXT, ordinal=0)]


def test_an_artifact_only_edit_is_detected_and_minted(tmp_path: Path) -> None:
    # The same case a prompt-only edit makes: `graph.yaml` stays byte-identical while the
    # baked content changed, so only the comparison over the parsed definition sees it.
    hub = build_hub(tmp_path)
    graph_yaml = _docketed(tmp_path, "docketed")
    _sync(hub, [graph_yaml])
    before = graph_yaml.read_text()

    hub.clock.advance(timedelta(minutes=1))  # distinct created_at → a deterministic newest
    (graph_yaml.parent / "reference-notes.md").write_text("# Docket\n\n- a revised term\n")
    outcomes = _sync(hub, [graph_yaml])

    assert graph_yaml.read_text() == before  # graph.yaml untouched
    assert _statuses(outcomes) == [("docketed", "minted")]
    graph_id = outcomes[0].graph_id
    assert graph_id is not None
    minted = hub.services.graphs.get(graph_id)
    assert minted is not None
    assert [a.content for a in minted.artifacts] == ["# Docket\n\n- a revised term\n"]


def test_a_graph_declaring_a_missing_artifact_file_is_reported_failed(tmp_path: Path) -> None:
    # A missing `artifacts:` file is a load-time error, so it surfaces here exactly
    # like an unparseable graph.yaml — as a per-graph `failed` row naming the entry.
    hub = build_hub(tmp_path)
    graph_yaml = _docketed(tmp_path, "missing-artifact", docket=None)

    outcomes = _sync(hub, [graph_yaml])

    assert _statuses(outcomes) == [("missing-artifact", "failed")]
    assert outcomes[0].detail is not None
    assert "docket" in outcomes[0].detail
    assert str(graph_yaml.parent / "reference-notes.md") in outcomes[0].detail


def test_a_failing_graph_does_not_stop_the_others_and_is_reported(tmp_path: Path) -> None:
    # A wheel shipping one bad graph must still converge the rest and say which one it
    # could not — the report is per graph, and only the bad row is `failed`.
    hub = build_hub(tmp_path)
    good_before = _packaged(tmp_path, "aaa-good")
    invalid = _packaged(tmp_path, "bbb-invalid", body="name: bbb-invalid\nentry: nowhere\nnodes: {}\n")
    unparseable = _packaged(tmp_path, "ccc-unparseable", body="name: ccc\nnodes: [oops\n")
    good_after = _packaged(tmp_path, "ddd-good")

    outcomes = _sync(hub, [good_before, invalid, unparseable, good_after])

    assert _statuses(outcomes) == [
        ("aaa-good", "minted"),
        ("bbb-invalid", "failed"),
        ("ccc-unparseable", "failed"),
        ("ddd-good", "minted"),
    ]
    assert outcomes[1].detail  # the validator's errors, not an empty string
    assert outcomes[2].name == "ccc-unparseable"  # named by its directory: it has no parsed name
    assert {g["name"] for g in hub.client.get("/api/graphs").json()} == {"aaa-good", "ddd-good"}


def test_an_in_flight_chunk_stays_on_the_definition_it_started_under(tmp_path: Path) -> None:
    # Reconciliation is additive: it supersedes a definition for *future* resolution and
    # never re-pins running work. Deliberate migration is #124/#164's, not this.
    hub = build_hub(tmp_path)
    path = _packaged(tmp_path, "inflight")
    minted = _sync(hub, [path])[0]
    pinned_graph_id = minted.graph_id

    chunk_id = hub.client.post("/api/chunks", json={"tokens": ["default:1"]}).json()["chunk_id"]
    assert hub.client.patch(f"/api/chunks/{chunk_id}", json={"graph_id": pinned_graph_id}).status_code == 202
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"] == pinned_graph_id

    hub.clock.advance(timedelta(minutes=1))
    (path.parent / "prompts" / "build.md").write_text("a whole new instruction")
    resynced = _sync(hub, [path])

    assert resynced[0].status is GraphSyncStatus.MINTED
    assert resynced[0].graph_id != pinned_graph_id
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"] == pinned_graph_id


def test_the_shipped_packaged_set_reconciles_and_then_reports_up_to_date(tmp_path: Path) -> None:
    """The real wheel, twice — the property a deploy leans on, and the guard that every
    shipped graph validates rather than failing at the operator's deploy."""
    hub = build_hub(tmp_path)
    paths = PACKAGED.paths
    assert paths, "the wheel ships at least one packaged graph"

    first = GraphReconciliation(hub.services.graph_mint, hub.services.graphs, paths).outcomes()
    second = GraphReconciliation(hub.services.graph_mint, hub.services.graphs, paths).outcomes()

    assert {o.status for o in first} == {GraphSyncStatus.MINTED}
    assert {o.status for o in second} == {GraphSyncStatus.UP_TO_DATE}


def test_sync_route_reports_every_graph_and_flags_ok(tmp_path: Path) -> None:
    # The route over the real packaged set — the deploy verb's own surface.
    hub = build_hub(tmp_path)

    first = hub.client.post("/api/graphs/sync", json={})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["ok"] is True
    assert {e["status"] for e in body["entries"]} == {"minted"}
    assert all(e["graph_id"] for e in body["entries"])

    second = hub.client.post("/api/graphs/sync", json={}).json()
    assert second["ok"] is True
    assert {e["status"] for e in second["entries"]} == {"up-to-date"}
    assert {e["name"] for e in second["entries"]} == {e["name"] for e in body["entries"]}


def test_sync_is_not_swallowed_by_the_graph_id_route(tmp_path: Path) -> None:
    """``/graphs/sync`` must not resolve as ``/graphs/{graph_id}`` with id ``sync``."""
    hub = build_hub(tmp_path)
    assert hub.client.get("/api/graphs/sync").status_code == 404  # no such graph id, by GET
    assert hub.client.post("/api/graphs/sync", json={}).status_code == 200  # the verb, by POST
