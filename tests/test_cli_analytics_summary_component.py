"""``blizzard hub analytics summary`` against the real router (component
tier): every dataset choice is reachable at the route the choice→route table
names, each dataset's applicable filters round-trip to a filtered result (mirroring
``events``), ``--ndjson`` streams the real per-chunk spend rollup, and the
applicability table matches each route's own declared query params."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path

import click
import httpx
import pytest
from click.testing import Result

from blizzard.hub.cli import hub as hub_group
from blizzard.hub.cli.analytics import _DATASETS
from tests.repo_files import repo_root
from tests.support import HubHarness
from tests.test_analytics_events_api import _cookie, _seeded_hub
from tests.test_analytics_spend_api import _mint_chunk, _push_usage
from tests.test_analytics_spend_api import _seeded_hub as _seeded_spend_hub
from tests.worker_http import Bound, bind_stubs

pytestmark = pytest.mark.component

_HUB_URL = "http://hub.local:8421"

with (repo_root() / "openapi/hub.openapi.json").open() as _f:
    _OPENAPI = json.load(_f)

#: The two flags every summary dataset can legally take that no route ever declares as
#: a query param in its own right — spend-chunks' pagination is real, but its own
#: buffered-route params (cursor/limit) are compared separately from the filter set.
_PAGINATION_PARAMS = {"cursor", "limit"}

#: The roll-up toggle the node and graph routes declare; the operator summary does not offer it.
_ROLLUP_PARAMS = {"by_name"}

#: The project lens every analytics route declares: the summary verb sends it for every dataset through its own
#: ``--project``, outside the per-dataset filter table.
_LENS_PARAMS = {"project"}


def _relay(hub: HubHarness, token: str) -> Bound:
    headers = _cookie(token)

    def fake_get(url: str, *, params: dict[str, str] | None = None, timeout: float, **_: object) -> httpx.Response:
        return hub.client.get(url, params=params, headers=headers)

    @contextlib.contextmanager
    def fake_stream(
        method: str, url: str, *, params: dict[str, str] | None = None, timeout: float, **_: object
    ) -> Iterator[httpx.Response]:
        with hub.client.stream(method.upper(), url, params=params, headers=headers) as resp:
            yield resp

    return bind_stubs(get=fake_get, stream=fake_stream)


def _invoke(bound: Bound, *args: str) -> Result:
    return bound.runner.invoke(hub_group, ["analytics", "summary", *args], env={"BZ_HUB_URL": _HUB_URL})


# --- the applicability table matches each route's own declared query params -------


def test_the_applicability_table_matches_the_openapi_declared_params() -> None:
    for dataset, spec in _DATASETS.items():
        declared = {p["name"] for p in _OPENAPI["paths"][spec.path]["get"]["parameters"]} - _ROLLUP_PARAMS
        assert declared >= _LENS_PARAMS, dataset
        table_filters = spec.filters
        assert table_filters == declared - _PAGINATION_PARAMS - _LENS_PARAMS, dataset
        assert (declared & _PAGINATION_PARAMS) == (_PAGINATION_PARAMS if spec.paginated else set()), dataset


def test_the_dataset_choice_list_matches_the_table() -> None:
    analytics = hub_group.get_command(click.Context(hub_group), "analytics")
    assert isinstance(analytics, click.Group)
    summary_command = analytics.commands["summary"]
    dataset_param = next(p for p in summary_command.params if p.name == "dataset")

    assert isinstance(dataset_param.type, click.Choice)
    assert set(dataset_param.type.choices) == set(_DATASETS)


# --- every dataset choice is reachable at the route the table names -------------------


def test_every_dataset_choice_is_reachable_at_the_real_router(tmp_path: Path) -> None:
    hub, token, _graph_id, _nodes = _seeded_spend_hub(tmp_path)
    bound = _relay(hub, token)

    for dataset, spec in _DATASETS.items():
        result = _invoke(bound, dataset, "--json")
        assert result.exit_code == 0, (dataset, result.output)
        assert spec.response_key in json.loads(result.stdout)


# --- filter round-trips: the events-derived counts datasets ---------------------------


def test_each_counts_datasets_applicable_filters_round_trip(tmp_path: Path) -> None:
    hub, token, _chunk_id = _seeded_hub(tmp_path)
    bound = _relay(hub, token)

    by_kind = _invoke(bound, "counts-nodes", "--kind", "file_read", "--json")
    assert json.loads(by_kind.stdout)["counts"] == [
        {"key": "nd_build", "count": 1, "graph_name": None, "node_name": None}
    ]

    by_tool = _invoke(bound, "counts-agent-types", "--tool", "Agent", "--json")
    assert json.loads(by_tool.stdout)["counts"] == []  # main-lane spawn: agent_type unset

    by_node = _invoke(bound, "counts-skills", "--node", "nd_build", "--json")
    assert json.loads(by_node.stdout)["counts"] == [
        {"key": "wf-commit", "count": 1, "graph_name": None, "node_name": None}
    ]

    by_prefix = _invoke(bound, "counts-files", "--subject-prefix", "src/", "--json")
    assert json.loads(by_prefix.stdout)["counts"] == [
        {"key": "src/a.py", "count": 1, "graph_name": None, "node_name": None}
    ]

    by_stale_version = _invoke(bound, "counts-files", "--extractor-version", "stale-version", "--json")
    assert json.loads(by_stale_version.stdout)["counts"] == []


# --- filter round-trip: the operational datasets' provenance filters, and spend-models ----


def test_spend_models_and_the_provenance_filters_round_trip(tmp_path: Path) -> None:
    hub, token, _graph_id, nodes = _seeded_spend_hub(tmp_path)
    bound = _relay(hub, token)
    chunk_id = _mint_chunk(hub, token)
    _push_usage(hub, chunk_id=chunk_id, node_id=nodes["build"], epoch=1, seq=1, cost_usd=0.1)

    by_model = _invoke(bound, "spend-models", "--json")
    assert [r["key"] for r in json.loads(by_model.stdout)["spend"]] == ["claude-opus-4-8"]

    kept = _invoke(bound, "spend-nodes", "--model", "claude-opus-4-8", "--json")
    assert [r["input_tokens"] for r in json.loads(kept.stdout)["spend"]] == [100]

    dropped = _invoke(bound, "spend-nodes", "--model", "other", "--json")
    assert json.loads(dropped.stdout)["spend"] == []
    for dataset in ("durations-nodes", "outcomes-nodes"):
        assert _invoke(bound, dataset, "--harness-id", "claude_code", "--json").exit_code == 0


# --- filter round-trip + --ndjson: spend-chunks, the one paginated/streamable dataset --


def test_spend_chunks_filters_round_trip_and_ndjson_streams_the_real_rollup(
    tmp_path: Path,
) -> None:
    hub, token, _graph_id, nodes = _seeded_spend_hub(tmp_path)
    bound = _relay(hub, token)
    chunk_a = _mint_chunk(hub, token, ref="1")
    chunk_b = _mint_chunk(hub, token, ref="2")
    _push_usage(hub, chunk_id=chunk_a, node_id=nodes["build"], epoch=1, seq=1, cost_usd=0.1)
    _push_usage(hub, chunk_id=chunk_b, node_id=nodes["build"], epoch=1, seq=2, cost_usd=0.2)

    paged = _invoke(bound, "spend-chunks", "--json")
    assert paged.exit_code == 0, paged.output
    paged_rows = json.loads(paged.stdout)["spend"]
    assert {r["chunk_id"] for r in paged_rows} == {chunk_a, chunk_b}

    limited = _invoke(bound, "spend-chunks", "--limit", "1", "--json")
    assert len(json.loads(limited.stdout)["spend"]) == 1

    streamed = _invoke(bound, "spend-chunks", "--ndjson")
    assert streamed.exit_code == 0, streamed.output
    lines = [json.loads(line) for line in streamed.stdout.strip().splitlines()]
    assert [r["chunk_id"] for r in lines] == [r["chunk_id"] for r in paged_rows]
