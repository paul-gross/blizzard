"""The runner's ``usage_since`` window read as the one spend figure (unit tier, real SQLite).

Each row is billed, estimated, or neither; only a row carrying neither amount makes the window
PARTIAL — the hub's ``UsageTotal`` reading. The same rows rendered by the hub CLI's ``Cost``, by
``spend_ceiling_reason`` and by ``spend_cap_detail`` read as one figure."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.hub.api.chunk_views import usage_total_view
from blizzard.hub.cli.views import Cost
from blizzard.hub.domain.chunk.model import UsageFact, UsageTotal
from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.lifecycle.model import spend_cap_detail, spend_cap_reached
from blizzard.runner.node_steps.chunk_state import ChunkSpend
from blizzard.runner.throttle.pause import spend_ceiling_reason
from tests.runner_fakes import make_store, record_usage

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)

type Row = tuple[float | None, float | None]

_BILLED: Row = (1.25, None)
_ESTIMATED: Row = (None, 2.5)
_NEITHER: Row = (None, None)


def _store_with(tmp_path: Path, rows: list[Row], *, at: datetime = _T0):
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    for i, (cost, estimate) in enumerate(rows):
        record_usage(
            store,
            lease_id=f"lease_{i}",
            chunk_id="ch_1",
            node_id="nd_build",
            epoch=1,
            generation=1,
            sample=UsageSample(
                kind="spawn",
                model="gpt-5",
                input_tokens=10,
                output_tokens=20,
                cache_read_tokens=0,
                cache_create_tokens=0,
                cost_usd=cost,
                estimated_cost_usd=estimate,
            ),
            recorded_at=at,
        )
    return store


@pytest.mark.parametrize(
    ("rows", "cost_usd", "estimated_cost_usd", "partial", "rendered"),
    [
        ([_BILLED], 1.25, None, False, "$1.25"),
        ([_ESTIMATED], 0.0, 2.5, False, "~$2.50"),
        ([_NEITHER], 0.0, None, True, "$0.00+"),
        ([_BILLED, _ESTIMATED], 1.25, 2.5, False, "~$3.75"),
        ([_BILLED, _NEITHER], 1.25, None, True, "$1.25+"),
        ([_BILLED, _ESTIMATED, _NEITHER], 1.25, 2.5, True, "~$3.75+"),
    ],
)
def test_each_window_reads_its_amount_and_partial_by_the_both_null_meaning(
    tmp_path: Path, rows, cost_usd, estimated_cost_usd, partial, rendered
):
    totals = _store_with(tmp_path, rows).usage_since(_T0)
    assert totals.cost_usd == pytest.approx(cost_usd)
    assert totals.estimated_cost_usd == (None if estimated_cost_usd is None else pytest.approx(estimated_cost_usd))
    assert totals.cost_partial is partial
    assert totals.spend_usd == pytest.approx(cost_usd + (estimated_cost_usd or 0.0))
    assert totals.figure.rendered() == rendered


def test_reaches_compares_billed_plus_estimated(tmp_path: Path) -> None:
    totals = _store_with(tmp_path, [_BILLED, _ESTIMATED]).usage_since(_T0)
    assert totals.reaches(3.75)
    assert not totals.reaches(3.76)


def test_rows_before_the_window_are_left_out(tmp_path: Path) -> None:
    store = _store_with(tmp_path, [_ESTIMATED, _NEITHER], at=_T0 - timedelta(seconds=1))
    totals = store.usage_since(_T0)
    assert (totals.cost_usd, totals.estimated_cost_usd, totals.cost_partial) == (0.0, None, False)


def test_every_surface_renders_the_same_rows_to_the_same_figure(tmp_path: Path) -> None:
    rows = [_BILLED, _ESTIMATED, _NEITHER]
    figure = "~$3.75+"

    runner_totals = _store_with(tmp_path, rows).usage_since(_T0)
    reason = spend_ceiling_reason(cap=3.0, window_hours=24.0, spend=runner_totals.figure)
    assert reason == f"spend ceiling $3.00 reached over the trailing 24h (spend {figure})"

    hub_total = UsageTotal.of(
        [
            UsageFact(
                node_id="nd_build",
                epoch=1,
                kind="spawn",
                model="gpt-5",
                input_tokens=10,
                output_tokens=20,
                cache_read_tokens=0,
                cache_create_tokens=0,
                cost_usd=cost,
                estimated_cost_usd=estimate,
                recorded_at=_T0,
            )
            for cost, estimate in rows
        ]
    )
    view = usage_total_view(hub_total)
    assert Cost.of(view.model_dump()).rendered == figure

    chunk = ChunkSpend(
        input_tokens=view.input_tokens,
        output_tokens=view.output_tokens,
        cache_read_tokens=view.cache_read_tokens,
        cache_create_tokens=view.cache_create_tokens,
        cost_usd=view.cost_usd,
        cost_partial=view.cost_partial,
        estimated_cost_usd=view.estimated_cost_usd,
    )
    assert spend_cap_reached(chunk, 3.0)
    assert spend_cap_detail(chunk, 3.0) == f"spend cap $3.00 reached (spend {figure})"
