"""``blizzard hub analytics summary`` against the real router (blizzard#257 Phase 3,
component tier): every dataset choice is reachable at the route the choice→route table
(D1) names, each dataset's applicable filters round-trip to a filtered result (mirroring
Phase 2's ``events``), ``--ndjson`` streams the real per-chunk spend rollup, and the
applicability table (D2) matches each route's own declared query params."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner, Result

import blizzard.hub.cli as hub_cli
from blizzard.hub.cli import _DATASETS
from blizzard.hub.cli import hub as hub_group
from tests.support import HubHarness
from tests.test_analytics_events_api import _cookie, _seeded_hub
from tests.test_analytics_spend_api import _mint_chunk, _push_usage
from tests.test_analytics_spend_api import _seeded_hub as _seeded_spend_hub

pytestmark = pytest.mark.component

_HUB_URL = "http://hub.local:8421"

with open("openapi/hub.openapi.json") as _f:
    _OPENAPI = json.load(_f)

#: The two flags every summary dataset can legally take that no route ever declares as
#: a query param in its own right — spend-chunks' pagination is real, but its own
#: buffered-route params (cursor/limit) are compared separately from the filter set.
_PAGINATION_PARAMS = {"cursor", "limit"}


def _relay(hub: HubHarness, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    headers = _cookie(token)

    def fake_get(url: str, *, params: dict[str, str] | None = None, timeout: float, **_: object) -> httpx.Response:
        return hub.client.get(url, params=params, headers=headers)

    @contextlib.contextmanager
    def fake_stream(
        method: str, url: str, *, params: dict[str, str] | None = None, timeout: float, **_: object
    ) -> Iterator[httpx.Response]:
        with hub.client.stream(method, url, params=params, headers=headers) as resp:
            yield resp

    monkeypatch.setattr(hub_cli.httpx, "get", fake_get)
    monkeypatch.setattr(hub_cli.httpx, "stream", fake_stream)


def _invoke(*args: str) -> Result:
    return CliRunner().invoke(hub_group, ["analytics", "summary", *args], env={"BZ_HUB_URL": _HUB_URL})


# --- D2: the applicability table matches each route's own declared query params -------


def test_the_applicability_table_matches_the_openapi_declared_params() -> None:
    for dataset, spec in _DATASETS.items():
        declared = {p["name"] for p in _OPENAPI["paths"][spec.path]["get"]["parameters"]}
        table_filters = spec.filters
        assert table_filters == declared - _PAGINATION_PARAMS, dataset
        assert (declared & _PAGINATION_PARAMS) == (_PAGINATION_PARAMS if spec.paginated else set()), dataset


def test_the_dataset_choice_list_matches_the_table() -> None:
    summary_command = hub_group.commands["analytics"].commands["summary"]  # type: ignore[attr-defined]
    dataset_param = next(p for p in summary_command.params if p.name == "dataset")

    assert set(dataset_param.type.choices) == set(_DATASETS)


# --- every dataset choice is reachable at the route the table names -------------------


def test_every_dataset_choice_is_reachable_at_the_real_router(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, token, _graph_id, _nodes = _seeded_spend_hub(tmp_path)
    _relay(hub, token, monkeypatch)

    for dataset, spec in _DATASETS.items():
        result = _invoke(dataset, "--json")
        assert result.exit_code == 0, (dataset, result.output)
        assert spec.response_key in json.loads(result.output)


# --- filter round-trips: the events-derived counts datasets ---------------------------


def test_each_counts_datasets_applicable_filters_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, token, _chunk_id = _seeded_hub(tmp_path)
    _relay(hub, token, monkeypatch)

    by_kind = _invoke("counts-nodes", "--kind", "file_read", "--json")
    assert json.loads(by_kind.output)["counts"] == [{"key": "nd_build", "count": 1}]

    by_tool = _invoke("counts-agent-types", "--tool", "Agent", "--json")
    assert json.loads(by_tool.output)["counts"] == []  # main-lane spawn: agent_type unset

    by_node = _invoke("counts-skills", "--node", "nd_build", "--json")
    assert json.loads(by_node.output)["counts"] == [{"key": "wf-commit", "count": 1}]

    by_prefix = _invoke("counts-files", "--subject-prefix", "src/", "--json")
    assert json.loads(by_prefix.output)["counts"] == [{"key": "src/a.py", "count": 1}]

    by_stale_version = _invoke("counts-files", "--extractor-version", "stale-version", "--json")
    assert json.loads(by_stale_version.output)["counts"] == []


# --- filter round-trip + --ndjson: spend-chunks, the one paginated/streamable dataset --


def test_spend_chunks_filters_round_trip_and_ndjson_streams_the_real_rollup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub, token, _graph_id, nodes = _seeded_spend_hub(tmp_path)
    _relay(hub, token, monkeypatch)
    chunk_a = _mint_chunk(hub, token, ref="1")
    chunk_b = _mint_chunk(hub, token, ref="2")
    _push_usage(hub, chunk_id=chunk_a, node_id=nodes["build"], epoch=1, seq=1, cost_usd=0.1)
    _push_usage(hub, chunk_id=chunk_b, node_id=nodes["build"], epoch=1, seq=2, cost_usd=0.2)

    paged = _invoke("spend-chunks", "--json")
    assert paged.exit_code == 0, paged.output
    paged_rows = json.loads(paged.output)["spend"]
    assert {r["chunk_id"] for r in paged_rows} == {chunk_a, chunk_b}

    limited = _invoke("spend-chunks", "--limit", "1", "--json")
    assert len(json.loads(limited.output)["spend"]) == 1

    streamed = _invoke("spend-chunks", "--ndjson")
    assert streamed.exit_code == 0, streamed.output
    lines = [json.loads(line) for line in streamed.output.strip().splitlines()]
    assert [r["chunk_id"] for r in lines] == [r["chunk_id"] for r in paged_rows]
