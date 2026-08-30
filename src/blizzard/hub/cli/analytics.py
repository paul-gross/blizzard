"""``blizzard hub analytics`` — blizzard#254: operator verbs over derived transcript-event analytics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import click

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.cli.command import FleetCommand
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.views import Cost, Listing


@dataclass(frozen=True)
class SpendRow:
    """One grouping key's spend rollup — the key column varies (``key`` for
    node/graph, ``chunk_id`` for the per-chunk dataset), so the caller names it."""

    row: dict[str, Any]
    key_field: str = "key"

    def line(self) -> str:
        row = self.row
        cost = Cost(row["cost_usd"], row["cost_partial"]).rendered
        return (
            f"{row[self.key_field]}  {cost}  in={row['input_tokens']} out={row['output_tokens']} "
            f"cache_read={row['cache_read_tokens']} cache_create={row['cache_create_tokens']}"
        )


class EventListing(Listing):
    empty = "no events"

    def line(self, row: Any) -> str:
        occurred = row.get("occurred_at") or "-"
        tool = f"  tool={row['tool']}" if row.get("tool") else ""
        subject = f"  {row['subject']}" if row.get("subject") else ""
        return f"{occurred}  {row['kind']:<16} chunk={row['chunk_id']} node={row['node_id']}{tool}{subject}"


class CountsListing(Listing):
    empty = "no counts"

    def line(self, row: Any) -> str:
        return f"{row['key']}: {row['count']}"


class DurationsListing(Listing):
    empty = "no durations"

    def line(self, row: Any) -> str:
        return (
            f"{row['key']}  steps={row['completed_steps']}  "
            f"total={row['total_seconds']:.1f}s  avg={row['avg_seconds']:.1f}s"
        )


class SpendListing(Listing):
    empty = "no spend rollups"

    def line(self, row: Any) -> str:
        return SpendRow(row).line()


class ChunkSpendListing(Listing):
    empty = "no chunk spend rollups"

    def line(self, row: Any) -> str:
        return SpendRow(row, key_field="chunk_id").line()


class OutcomesListing(Listing):
    empty = "no outcomes"

    def line(self, row: Any) -> str:
        choices = ", ".join(f"{name}={count}" for name, count in row["choice_counts"].items())
        return f"{row['node_id']}  choices=[{choices}]  attempt_failures={row['attempt_failures']}"


@click.group("analytics")
def analytics_group() -> None:
    """Operator verbs over derived transcript-event analytics."""


@analytics_group.command("re-derive", cls=FleetCommand)
@click.option("--segment", "segment_id", default=None, help="Force one segment, regardless of its candidacy.")
@click.option("--chunk", "chunk_id", default=None, help="Every candidate segment of one chunk.")
@click.option("--limit", "limit", default=50, show_default=True, help="Cap on segments derived by one call.")
def analytics_re_derive(cli: CliContext, segment_id: str | None, chunk_id: str | None, limit: int) -> None:
    """Force the standing derivation sweep's own replacement unit now, rather than
    waiting for its next tick — scoped to one segment, one chunk, or every candidate
    (neither option given). No downtime: it runs the same in-process reconciler already
    live, just driven directly. Prints ``derived``/``remaining``; a nonzero ``remaining``
    on a chunk/all-scoped call means running it again continues from where it left off."""
    if segment_id is not None and chunk_id is not None:
        raise click.ClickException("--segment and --chunk are mutually exclusive")
    body: dict[str, object] = {"limit": limit}
    if segment_id is not None:
        body["segment_id"] = segment_id
    if chunk_id is not None:
        body["chunk_id"] = chunk_id
    resp = cli.post("/api/analytics/re-derive", "POST /analytics/re-derive", json_body=body)
    result = resp.json()
    cli.show_lines(result, f"derived {result['derived']}, {result['remaining']} remaining in scope")


# `blizzard hub analytics events`/`summary` — shared filter options (blizzard#257 D2): one
# declaration per flag, so each verb's command stacks whichever of them its dataset(s) take.


def _graph_option(f: Any) -> Any:
    return click.option("--graph", "graph_id", default=None, help="Scope to one workflow graph.")(f)


def _source_option(f: Any) -> Any:
    return click.option("--source", default=None, help="Scope to one work source.")(f)


def _since_option(f: Any) -> Any:
    return click.option(
        "--since",
        default=None,
        type=click.DateTime(),
        help="Only records at/after this instant, read in the operator's own local time.",
    )(f)


def _until_option(f: Any) -> Any:
    return click.option(
        "--until",
        default=None,
        type=click.DateTime(),
        help="Only records before this instant, read in the operator's own local time.",
    )(f)


def _extractor_version_option(f: Any) -> Any:
    return click.option(
        "--extractor-version",
        "extractor_version",
        default=None,
        help="The event-extraction version to read (defaults to the current version).",
    )(f)


def _kind_option(f: Any) -> Any:
    return click.option("--kind", default=None, help="Narrow to one event kind.")(f)


def _tool_option(f: Any) -> Any:
    return click.option("--tool", default=None, help="Narrow to one tool name.")(f)


def _subject_prefix_option(f: Any) -> Any:
    return click.option(
        "--subject-prefix", "subject_prefix", default=None, help="Narrow to subjects with this prefix."
    )(f)


def _node_option(f: Any) -> Any:
    return click.option("--node", "node_id", default=None, help="Narrow to one node id.")(f)


def _utc_query_value(value: datetime | None) -> str | None:
    """D6: a bare ``--since``/``--until`` is read as the operator's own local wall clock,
    not UTC — converted (not merely relabeled) before it crosses the wire."""
    return iso_utc(value.astimezone(UTC)) if value is not None else None


def _scope_params(
    *,
    graph_id: str | None,
    source: str | None,
    since: datetime | None,
    until: datetime | None,
    extractor_version: str | None = None,
    kind: str | None = None,
    tool: str | None = None,
    subject_prefix: str | None = None,
    node_id: str | None = None,
) -> dict[str, str]:
    """Every named filter as a query param, omitting whichever were left unset."""
    named = {
        "graph_id": graph_id,
        "source": source,
        "since": _utc_query_value(since),
        "until": _utc_query_value(until),
        "extractor_version": extractor_version,
        "kind": kind,
        "tool": tool,
        "subject_prefix": subject_prefix,
        "node_id": node_id,
    }
    return {k: v for k, v in named.items() if v is not None}


@analytics_group.command("events", cls=FleetCommand)
@_graph_option
@_source_option
@_since_option
@_until_option
@_extractor_version_option
@_kind_option
@_tool_option
@_subject_prefix_option
@_node_option
@click.option("--cursor", default=None, help="Resume from a prior page's next_cursor.")
@click.option(
    "--limit", default=None, type=int, help="Max rows in one page (1-1000, default 200). Illegal with --ndjson."
)
@click.option(
    "--ndjson",
    is_flag=True,
    default=False,
    help="Stream every matching event as NDJSON to stdout, unpaged. Incompatible with --json/--cursor/--limit.",
)
def analytics_events(
    cli: CliContext,
    graph_id: str | None,
    source: str | None,
    since: datetime | None,
    until: datetime | None,
    extractor_version: str | None,
    kind: str | None,
    tool: str | None,
    subject_prefix: str | None,
    node_id: str | None,
    cursor: str | None,
    limit: int | None,
    ndjson: bool,
) -> None:
    """Read the derived transcript-event projection (blizzard#255), every ``/events``
    filter as a flag: a bounded page by default, or the whole filtered set streamed as
    NDJSON to stdout under ``--ndjson``."""
    if ndjson:
        if cli.as_json:
            raise click.UsageError("--ndjson is incompatible with --json")
        if cursor is not None:
            raise click.UsageError("--ndjson is incompatible with --cursor")
        if limit is not None:
            raise click.UsageError("--ndjson is incompatible with --limit")
    params = _scope_params(
        graph_id=graph_id,
        source=source,
        since=since,
        until=until,
        extractor_version=extractor_version,
        kind=kind,
        tool=tool,
        subject_prefix=subject_prefix,
        node_id=node_id,
    )
    if ndjson:
        for line in cli.stream("/api/analytics/events/ndjson", "GET /analytics/events/ndjson", params=params):
            click.echo(line)
        return
    if cursor is not None:
        params["cursor"] = cursor
    params["limit"] = str(limit if limit is not None else 200)
    body = cli.get("/api/analytics/events", "GET /analytics/events", params=params).json()
    cli.show(body, EventListing(body["events"]))


# `blizzard hub analytics summary` — blizzard#257 D1/D2: one verb over the ten read
# rollup routes, an explicit dataset→route table (a `-`↔`/` rule would 404 on
# `agent-types`), and an explicit per-dataset filter-applicability table — a filter
# inapplicable to the chosen dataset is refused, never silently dropped.

#: The four scope filters every dataset takes — the common ground D2 builds each
#: dataset's own applicable set on top of.
_SCOPE_FILTERS = frozenset({"graph_id", "source", "since", "until"})

#: The flag each filter's dest name renders as, for a per-dataset applicability error.
_FLAG_NAMES = {
    "graph_id": "--graph",
    "source": "--source",
    "since": "--since",
    "until": "--until",
    "extractor_version": "--extractor-version",
    "kind": "--kind",
    "tool": "--tool",
    "subject_prefix": "--subject-prefix",
    "node_id": "--node",
}


@dataclass(frozen=True)
class _Dataset:
    """One ``summary`` choice: its route, the key its envelope carries the rows under,
    the view that renders them, and which of the shared filters it honors. Only
    ``spend-chunks`` paginates or streams — the one rollup with a bulk-export sibling."""

    path: str
    response_key: str
    view: type[Listing]
    filters: frozenset[str]
    paginated: bool = False
    streamable: bool = False


#: Mints the ten read rollup routes' own criteria types, mirroring each route's own
#: declared query params (``api/analytics.py``) — not derived from the path, so a
#: hyphenated route (``counts/agent-types``) never round-trips through a naive rule.
_DATASETS: dict[str, _Dataset] = {
    "counts-files": _Dataset(
        "/api/analytics/counts/files",
        "counts",
        CountsListing,
        _SCOPE_FILTERS | {"extractor_version", "tool", "subject_prefix", "node_id"},
    ),
    "counts-skills": _Dataset(
        "/api/analytics/counts/skills", "counts", CountsListing, _SCOPE_FILTERS | {"extractor_version", "node_id"}
    ),
    "counts-agent-types": _Dataset(
        "/api/analytics/counts/agent-types",
        "counts",
        CountsListing,
        _SCOPE_FILTERS | {"extractor_version", "kind", "tool", "subject_prefix", "node_id"},
    ),
    "counts-nodes": _Dataset(
        "/api/analytics/counts/nodes",
        "counts",
        CountsListing,
        _SCOPE_FILTERS | {"extractor_version", "kind", "tool", "subject_prefix"},
    ),
    "durations-nodes": _Dataset("/api/analytics/durations/nodes", "durations", DurationsListing, _SCOPE_FILTERS),
    "durations-graphs": _Dataset("/api/analytics/durations/graphs", "durations", DurationsListing, _SCOPE_FILTERS),
    "spend-nodes": _Dataset("/api/analytics/spend/nodes", "spend", SpendListing, _SCOPE_FILTERS),
    "spend-graphs": _Dataset("/api/analytics/spend/graphs", "spend", SpendListing, _SCOPE_FILTERS),
    "spend-chunks": _Dataset(
        "/api/analytics/spend/chunks",
        "spend",
        ChunkSpendListing,
        _SCOPE_FILTERS,
        paginated=True,
        streamable=True,
    ),
    "outcomes-nodes": _Dataset("/api/analytics/outcomes/nodes", "outcomes", OutcomesListing, _SCOPE_FILTERS),
}

_SPEND_CHUNKS_NDJSON_PATH = "/api/analytics/spend/chunks/ndjson"


@analytics_group.command("summary", cls=FleetCommand)
@click.argument("dataset", type=click.Choice(sorted(_DATASETS)))
@_graph_option
@_source_option
@_since_option
@_until_option
@_extractor_version_option
@_kind_option
@_tool_option
@_subject_prefix_option
@_node_option
@click.option("--cursor", default=None, help="Resume from a prior page's next_cursor (spend-chunks only).")
@click.option(
    "--limit", default=None, type=int, help="Max rows in one page (spend-chunks only). Illegal with --ndjson."
)
@click.option(
    "--ndjson",
    is_flag=True,
    default=False,
    help="Stream spend-chunks as NDJSON to stdout, unpaged (spend-chunks only). "
    "Incompatible with --json/--cursor/--limit.",
)
def analytics_summary(
    cli: CliContext,
    dataset: str,
    graph_id: str | None,
    source: str | None,
    since: datetime | None,
    until: datetime | None,
    extractor_version: str | None,
    kind: str | None,
    tool: str | None,
    subject_prefix: str | None,
    node_id: str | None,
    cursor: str | None,
    limit: int | None,
    ndjson: bool,
) -> None:
    """Read one of the canned counts or operational-dataset rollups (blizzard#255/#256):
    the four DATASET counts (counts-files, counts-skills, counts-agent-types,
    counts-nodes) and the six operational datasets (durations-nodes, durations-graphs,
    spend-nodes, spend-graphs, spend-chunks, outcomes-nodes). Only spend-chunks pages
    or streams; a filter DATASET's own route does not expose is refused."""
    spec = _DATASETS[dataset]
    given = {
        "graph_id": graph_id,
        "source": source,
        "since": since,
        "until": until,
        "extractor_version": extractor_version,
        "kind": kind,
        "tool": tool,
        "subject_prefix": subject_prefix,
        "node_id": node_id,
    }
    for name, value in given.items():
        if value is not None and name not in spec.filters:
            raise click.UsageError(f"{_FLAG_NAMES[name]} does not apply to dataset {dataset!r}")
    if cursor is not None and not spec.paginated:
        raise click.UsageError(f"--cursor does not apply to dataset {dataset!r}")
    if limit is not None and not spec.paginated:
        raise click.UsageError(f"--limit does not apply to dataset {dataset!r}")
    if ndjson and not spec.streamable:
        raise click.UsageError(f"--ndjson does not apply to dataset {dataset!r}")
    if ndjson:
        if cli.as_json:
            raise click.UsageError("--ndjson is incompatible with --json")
        if cursor is not None:
            raise click.UsageError("--ndjson is incompatible with --cursor")
        if limit is not None:
            raise click.UsageError("--ndjson is incompatible with --limit")
    params = _scope_params(
        graph_id=graph_id,
        source=source,
        since=since,
        until=until,
        extractor_version=extractor_version,
        kind=kind,
        tool=tool,
        subject_prefix=subject_prefix,
        node_id=node_id,
    )
    if ndjson:
        for line in cli.stream(_SPEND_CHUNKS_NDJSON_PATH, "GET /analytics/spend/chunks/ndjson", params=params):
            click.echo(line)
        return
    if cursor is not None:
        params["cursor"] = cursor
    if spec.paginated:
        params["limit"] = str(limit if limit is not None else 200)
    operation = f"GET {spec.path.removeprefix('/api')}"
    body = cli.get(spec.path, operation, params=params).json()
    cli.show(body, spec.view(body[spec.response_key]))
