"""``blizzard hub analytics summary`` (blizzard#257 Phase 3) — a pure client of the ten
read rollup routes driven with ``httpx`` stubbed (unit tier): each response shape's
rendering, ``--json``, the per-dataset filter-applicability guards (D2), and
``--ndjson``'s spend-chunks-only guard plus its incompatible-flag guards (mirroring
Phase 2's ``events``)."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator

import httpx
import pytest
from click.testing import CliRunner

from blizzard.hub.cli import hub as hub_group

pytestmark = pytest.mark.unit


class _FakeResponse:
    def __init__(self, status_code: int, payload: object | None = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)  # type: ignore[arg-type]


class _FakeStreamResponse(_FakeResponse):
    def __init__(self, status_code: int, lines: list[str] | None = None, payload: object | None = None) -> None:
        super().__init__(status_code, payload)
        self._lines = lines or []

    def read(self) -> None:
        pass

    def iter_lines(self) -> Iterator[str]:
        yield from self._lines


def _stream_returning(resp: _FakeStreamResponse):
    @contextlib.contextmanager
    def fake_stream(method: str, url: str, **kwargs: object) -> Iterator[_FakeStreamResponse]:
        yield resp

    return fake_stream


def _get_returning(body: object):
    calls: list[dict[str, str]] = []

    def fake_get(url: str, *, params: dict[str, str] | None = None, timeout: float, **_: object) -> _FakeResponse:
        calls.append(params or {})
        return _FakeResponse(200, body)

    return fake_get, calls


# --- one Listing per distinct response shape ------------------------------------------


def test_counts_dataset_renders_the_counts_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_get, _ = _get_returning({"counts": [{"key": "src/a.py", "count": 3}]})
    monkeypatch.setattr(httpx, "get", fake_get)

    result = CliRunner().invoke(hub_group, ["analytics", "summary", "counts-files"])

    assert result.exit_code == 0, result.output
    assert "src/a.py: 3" in result.output


def test_durations_dataset_renders_the_durations_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_get, _ = _get_returning(
        {"durations": [{"key": "nd_build", "completed_steps": 2, "total_seconds": 90.0, "avg_seconds": 45.0}]}
    )
    monkeypatch.setattr(httpx, "get", fake_get)

    result = CliRunner().invoke(hub_group, ["analytics", "summary", "durations-nodes"])

    assert result.exit_code == 0, result.output
    assert "nd_build" in result.output
    assert "steps=2" in result.output
    assert "avg=45.0s" in result.output


def test_spend_dataset_renders_the_spend_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_get, _ = _get_returning(
        {
            "spend": [
                {
                    "key": "nd_build",
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_tokens": 0,
                    "cache_create_tokens": 0,
                    "cost_usd": 1.5,
                    "cost_partial": False,
                }
            ]
        }
    )
    monkeypatch.setattr(httpx, "get", fake_get)

    result = CliRunner().invoke(hub_group, ["analytics", "summary", "spend-nodes"])

    assert result.exit_code == 0, result.output
    assert "nd_build" in result.output
    assert "$1.50" in result.output


def test_spend_chunks_dataset_renders_the_chunk_spend_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_get, _ = _get_returning(
        {
            "spend": [
                {
                    "chunk_id": "ch_1",
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_tokens": 0,
                    "cache_create_tokens": 0,
                    "cost_usd": 0.02,
                    "cost_partial": True,
                }
            ],
            "next_cursor": None,
        }
    )
    monkeypatch.setattr(httpx, "get", fake_get)

    result = CliRunner().invoke(hub_group, ["analytics", "summary", "spend-chunks"])

    assert result.exit_code == 0, result.output
    assert "ch_1" in result.output
    assert "~$0.02" in result.output


def test_outcomes_dataset_renders_the_outcomes_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_get, _ = _get_returning(
        {"outcomes": [{"node_id": "nd_build", "choice_counts": {"pass": 3, "fail": 1}, "attempt_failures": 2}]}
    )
    monkeypatch.setattr(httpx, "get", fake_get)

    result = CliRunner().invoke(hub_group, ["analytics", "summary", "outcomes-nodes"])

    assert result.exit_code == 0, result.output
    assert "nd_build" in result.output
    assert "pass=3" in result.output
    assert "attempt_failures=2" in result.output


def test_json_prints_the_raw_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"counts": [{"key": "wf-commit", "count": 1}]}
    fake_get, _ = _get_returning(body)
    monkeypatch.setattr(httpx, "get", fake_get)

    result = CliRunner().invoke(hub_group, ["analytics", "summary", "counts-skills", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == body


# --- D2: the per-dataset filter-applicability table -----------------------------------


def test_a_filter_the_dataset_does_not_expose_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("must not reach the hub"))

    # counts-nodes does not offer --node (it would select a single group).
    result = CliRunner().invoke(hub_group, ["analytics", "summary", "counts-nodes", "--node", "nd_build"])

    assert result.exit_code != 0
    assert "--node does not apply to dataset 'counts-nodes'" in result.output


def test_kind_is_refused_for_a_fixed_kind_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("must not reach the hub"))

    result = CliRunner().invoke(hub_group, ["analytics", "summary", "counts-files", "--kind", "file_read"])

    assert result.exit_code != 0
    assert "--kind does not apply to dataset 'counts-files'" in result.output


def test_extractor_version_is_refused_for_an_operational_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("must not reach the hub"))

    result = CliRunner().invoke(hub_group, ["analytics", "summary", "durations-nodes", "--extractor-version", "v2"])

    assert result.exit_code != 0
    assert "--extractor-version does not apply to dataset 'durations-nodes'" in result.output


def test_cursor_is_refused_outside_spend_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("must not reach the hub"))

    result = CliRunner().invoke(hub_group, ["analytics", "summary", "spend-nodes", "--cursor", "cur_1"])

    assert result.exit_code != 0
    assert "--cursor does not apply to dataset 'spend-nodes'" in result.output


def test_a_scope_filter_applies_to_every_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_get, calls = _get_returning({"outcomes": []})
    monkeypatch.setattr(httpx, "get", fake_get)

    result = CliRunner().invoke(hub_group, ["analytics", "summary", "outcomes-nodes", "--graph", "gr_1"])

    assert result.exit_code == 0, result.output
    assert calls[0]["graph_id"] == "gr_1"


def test_the_default_limit_is_200_for_spend_chunks_only(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_get, calls = _get_returning({"spend": [], "next_cursor": None})
    monkeypatch.setattr(httpx, "get", fake_get)

    result = CliRunner().invoke(hub_group, ["analytics", "summary", "spend-chunks"])

    assert result.exit_code == 0, result.output
    assert calls[0]["limit"] == "200"


def test_non_paginated_datasets_get_no_limit_param(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_get, calls = _get_returning({"outcomes": []})
    monkeypatch.setattr(httpx, "get", fake_get)

    result = CliRunner().invoke(hub_group, ["analytics", "summary", "outcomes-nodes"])

    assert result.exit_code == 0, result.output
    assert "limit" not in calls[0]


# --- --ndjson: spend-chunks only, and the same incompatible-flag guards ----------------


def test_ndjson_streams_spend_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _FakeStreamResponse(200, lines=['{"chunk_id": "ch_1"}'])
    monkeypatch.setattr(httpx, "stream", _stream_returning(resp))
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("--ndjson must not hit the paged route"))

    result = CliRunner().invoke(hub_group, ["analytics", "summary", "spend-chunks", "--ndjson"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ['{"chunk_id": "ch_1"}']


def test_ndjson_is_refused_outside_spend_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: pytest.fail("must not reach the hub"))
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("must not reach the hub"))

    result = CliRunner().invoke(hub_group, ["analytics", "summary", "spend-nodes", "--ndjson"])

    assert result.exit_code != 0
    assert "--ndjson does not apply to dataset 'spend-nodes'" in result.output


def test_ndjson_rejects_json_on_spend_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: pytest.fail("must not reach the hub"))

    result = CliRunner().invoke(hub_group, ["analytics", "summary", "spend-chunks", "--ndjson", "--json"])

    assert result.exit_code != 0
    assert "--ndjson is incompatible with --json" in result.output


def test_ndjson_rejects_cursor_on_spend_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: pytest.fail("must not reach the hub"))

    result = CliRunner().invoke(hub_group, ["analytics", "summary", "spend-chunks", "--ndjson", "--cursor", "cur_1"])

    assert result.exit_code != 0
    assert "--ndjson is incompatible with --cursor" in result.output


def test_ndjson_rejects_limit_on_spend_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: pytest.fail("must not reach the hub"))

    result = CliRunner().invoke(hub_group, ["analytics", "summary", "spend-chunks", "--ndjson", "--limit", "10"])

    assert result.exit_code != 0
    assert "--ndjson is incompatible with --limit" in result.output


# --- an unknown dataset is refused by click.Choice ------------------------------------


def test_an_unknown_dataset_is_rejected() -> None:
    result = CliRunner().invoke(hub_group, ["analytics", "summary", "not-a-real-dataset"])

    assert result.exit_code != 0
    assert "not-a-real-dataset" in result.output
