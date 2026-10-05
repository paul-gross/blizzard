"""The egress contract (unit tier): ``contracts/egress/`` pins the ``steps``, ``invocations`` and ``events`` datasets and
the views each declares, and this module binds the code schemas, the writer's output, the generated ``_schema`` documents and the published page to it.

``dictionary.json`` and the view SQL are authored; ``golden/`` and ``_schema/`` and the generated block of
``docs/deployment/egress.md`` are written only by regeneration."""

from __future__ import annotations

import gzip
import json
import os
import re
from dataclasses import fields, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.parquet as pq
import pytest

from blizzard.hub.domain.observability.analytics.events import (
    DerivationMarker,
    DropFact,
    SegmentProvenance,
    TranscriptEvent,
)
from blizzard.hub.domain.observability.egress.event_rows import (
    RECORD_DERIVATION,
    RECORD_DROPPED,
    RECORD_EVENT,
    EventDerivation,
    ExportedEventsEntry,
    FilePathPolicy,
    derivation_rows,
    dropped_row,
)
from blizzard.hub.domain.observability.egress.repository import EventsPosition
from blizzard.hub.domain.observability.egress.rows import AttributedUsage, ExportedInvocation, ExportedStep, step_row
from blizzard.hub.domain.observability.egress.schema import (
    EVENTS_SCHEMA,
    INVOCATIONS_SCHEMA,
    STEPS_SCHEMA,
    events_egress_row,
    invocation_egress_row,
    step_egress_row,
)
from blizzard.hub.domain.observability.tracing.cursor import CursorKey
from blizzard.hub.domain.observability.tracing.steps import PrecededBy, StepKind, StepOutcome, identify_steps
from blizzard.hub.domain.observability.tracing.summary import summarize_step
from blizzard.hub.egress.internal.files import DirectoryEgressWriter, schema_document
from blizzard.hub.egress.internal.ndjson import NdjsonEncoder
from blizzard.hub.egress.internal.parquet import ParquetEncoder, arrow_schema
from blizzard.hub.egress.writer import (
    MONEY_SCALE,
    ColumnSpec,
    ColumnType,
    DatasetSchema,
    EgressBatch,
    EgressPass,
    EgressValues,
    EgressWriterSettings,
    PlacedFile,
    rfc3339_utc,
)
from tests import trace_fixtures as fx
from tests.egress_recipes import COST_BY_NODE_BY_DAY, SLOWEST_STATION_OF_THE_WEEK, recipe
from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_ROOT = repo_root()
_CONTRACT_DIR = _ROOT / "contracts" / "egress"
_SCENARIO = "two-pass"
_GOLDEN_DIR = _CONTRACT_DIR / "golden" / _SCENARIO
_SCHEMA_DIR = _CONTRACT_DIR / "_schema"
_DOC = _ROOT / "docs" / "deployment" / "egress.md"
_VERSIONING_DOC = _ROOT / "docs" / "versioning.md"
_REGEN_VARIABLE = "BLIZZARD_REGEN_EGRESS_CONTRACT"
_REGEN_COMMAND = f"{_REGEN_VARIABLE}=1 uv run pytest tests/test_egress_contract.py"
_BEGIN = "<!-- egress-dictionary:begin -->"
_END = "<!-- egress-dictionary:end -->"
_TOKEN = "gold"
_FIRST_PASS = datetime(2026, 1, 2, 0, 0, tzinfo=UTC)
_SECOND_PASS = datetime(2026, 1, 3, 0, 0, tzinfo=UTC)
_SETTINGS = EgressWriterSettings(max_rows_per_file=1000, min_free_bytes=0)
_CODE_SCHEMAS = {"steps": STEPS_SCHEMA, "invocations": INVOCATIONS_SCHEMA, "events": EVENTS_SCHEMA}
_DATASET_NAMES = list(_CODE_SCHEMAS)
_RECORD_TYPES = [RECORD_DERIVATION, RECORD_EVENT, RECORD_DROPPED]
_CODE_ENUMS: dict[tuple[str, str], set[str]] = {
    ("steps", "step_kind"): {e.value for e in StepKind},
    ("steps", "outcome"): {e.value for e in StepOutcome},
    ("steps", "preceded_by"): {e.value for e in PrecededBy},
    ("events", "record_type"): set(_RECORD_TYPES),
}


def _dictionary() -> dict[str, Any]:
    return json.loads((_CONTRACT_DIR / "dictionary.json").read_text())


def _datasets() -> dict[str, dict[str, Any]]:
    return {d["name"]: d for d in _dictionary()["datasets"]}


def _contract_schema(dataset: dict[str, Any]) -> DatasetSchema:
    columns = tuple(
        ColumnSpec(c["name"], ColumnType(c["type"]), c["nullable"], c["meaning"]) for c in dataset["columns"]
    )
    return DatasetSchema(dataset["name"], dataset["major_version"], columns)


def _contract_schemas() -> dict[str, DatasetSchema]:
    return {name: _contract_schema(d) for name, d in _datasets().items()}


def _view_sql(view: str) -> str:
    return (_CONTRACT_DIR / f"{view}.sql").read_text().strip()


def _views() -> list[tuple[str, str]]:
    """Every view the dictionary declares, as ``(dataset, view)`` in dictionary order."""
    return [(name, view) for name, d in _datasets().items() for view in d["views"]]


# -- the seeded scenario ---------------------------------------------------------------------------------------------


def _scenario_rows() -> tuple[list[tuple[ExportedStep, CursorKey]], list[ExportedInvocation]]:
    """Every closed step and every usage fact of the shared tracing scenarios, as the first pass exports them."""
    steps: list[tuple[ExportedStep, CursorKey]] = []
    invocations: list[ExportedInvocation] = []
    usage_id = 0
    for name, scenario in fx.scenarios().items():
        facts = replace(scenario, chunk_id=f"ch_{name}")
        found = identify_steps(facts)
        for step in found:
            if step.close is None:
                continue
            row = step_row(summarize_step(facts, step, found), _FIRST_PASS)
            steps.append((row, CursorKey.of(step)))
        for fact in facts.usage:
            usage_id += 1
            try:
                invocations.append(
                    fx.invocation_of(facts, AttributedUsage(usage_id, facts.chunk_id, "r-1", fact), _FIRST_PASS)
                )
            except LookupError:
                continue
    return steps, invocations


def _rewritten(
    steps: list[tuple[ExportedStep, CursorKey]], invocations: list[ExportedInvocation]
) -> tuple[list[tuple[ExportedStep, CursorKey]], list[ExportedInvocation]]:
    """The second pass: one step and one invocation exported again, newer and with changed values, as a late-usage
    rewrite produces."""
    step, key = next((s, k) for s, k in steps if s.invocations > 0)
    again = replace(
        step, input_tokens=step.input_tokens + 7, invocations=step.invocations + 1, exported_at=_SECOND_PASS
    )
    invocation = invocations[0]
    later = replace(invocation, output_tokens=invocation.output_tokens + 3, exported_at=_SECOND_PASS)
    return [(again, key)], [later]


_OLD_VERSION = "blizzard-analytics/3"
_NEW_VERSION = "blizzard-analytics/4"
_PATHS = FilePathPolicy("absolute")
_PROVENANCE = SegmentProvenance("claude_code", "2.1.0", "claude-sonnet-5-5", None)
_EVENTS_CHUNK = "ch_runner-step"


def _hour(day: int, hour: int) -> datetime:
    return datetime(2026, 1, day, hour, 0, tzinfo=UTC)


def _event(kind: str, subject: str | None, turn: str, occurrence: int = 0, depth: int = 0) -> TranscriptEvent:
    return TranscriptEvent(
        kind=kind,
        turn_path=turn,
        occurrence=occurrence,
        payload="{}",
        subject=subject,
        tool={"file_read": "Read", "skill_invocation": "Skill", "agent_spawn": "Agent"}[kind],
        chunk_id=_EVENTS_CHUNK,
        node_id="n",
        epoch=1,
        spawn_generation=1,
        graph_id="g1",
        depth=depth,
        agent_type="reviewer" if depth else None,
        occurred_at=None,
    )


def _derivation(segment: str, version: str, derived_at: datetime, *events: TranscriptEvent) -> EventDerivation:
    marker = DerivationMarker(segment, version, "fingerprint", derived_at, len(events), True)
    return EventDerivation(marker, _PROVENANCE, events, _EVENTS_CHUNK, 1, 1, "/work")


def _read(path: str, turn: str) -> TranscriptEvent:
    return _event("file_read", path, turn)


# The segments of the events scenario, and what each exercises. Nothing here is re-derived by a second implementation
# of the views' rule: the expected outcome of each is stated beside its test.
_UPGRADED = _derivation("seg-upgraded", _NEW_VERSION, _hour(2, 1), _read("new.py", "t1"))
_UPGRADED_FROM = _derivation(
    "seg-upgraded", _OLD_VERSION, _hour(1, 1), _read("old.py", "t1"), _event("skill_invocation", "lint", "t2")
)
_OLD_ONLY = _derivation("seg-old-only", _OLD_VERSION, _hour(1, 2), _read("kept.py", "t1"))
_EMPTIED_FROM = _derivation("seg-emptied", _OLD_VERSION, _hour(1, 3), _read("gone.py", "t1"))
_EMPTIED = _derivation("seg-emptied", _NEW_VERSION, _hour(2, 3))
_DROPPED = _derivation("seg-dropped", _OLD_VERSION, _hour(1, 4), _read("dropped.py", "t1"))
_REVIVED_FROM = _derivation("seg-revived", _OLD_VERSION, _hour(1, 5), _read("before.py", "t1"))
_REVIVED = _derivation("seg-revived", _NEW_VERSION, _hour(2, 12), _read("after.py", "t1"))
_TWICE = _derivation("seg-twice", _OLD_VERSION, _hour(1, 6), _read("once.py", "t1"))
_KINDS = _derivation(
    "seg-kinds", _OLD_VERSION, _hour(1, 7), _read("both.py", "t1"), _event("agent_spawn", "reviewer", "t1")
)
_SIDECHAIN = _derivation(
    "seg-sidechain",
    _OLD_VERSION,
    _hour(1, 8),
    _read("main.py", "t1"),
    _event("file_read", "deep.py", "t1/s1/s2", depth=2),
)
_REDERIVED_FROM = _derivation("seg-rederived", _NEW_VERSION, _hour(2, 7), _read("first.py", "t1"))
_REDERIVED = _derivation("seg-rederived", _NEW_VERSION, _hour(2, 8), _read("second.py", "t1"))

_DROPS = {
    "seg-dropped": DropFact("seg-dropped", _EVENTS_CHUNK, 1, 1, _hour(2, 4)),
    "seg-revived": DropFact("seg-revived", _EVENTS_CHUNK, 1, 1, _hour(2, 5)),
}

_EVENTS_FIRST = [_UPGRADED_FROM, _OLD_ONLY, _EMPTIED_FROM, _DROPPED, _REVIVED_FROM, _TWICE, _KINDS, _SIDECHAIN]
_EVENTS_SECOND = [_UPGRADED, _EMPTIED, _REVIVED, _TWICE, _REDERIVED_FROM, _REDERIVED]
_EVENTS_SECOND_DROPS = [_DROPS["seg-dropped"], _DROPS["seg-revived"]]


def _events_facts() -> Any:
    return replace(fx.scenarios()["runner-step"], chunk_id=_EVENTS_CHUNK)


def _events_pass_rows(
    derivations: list[EventDerivation], drops: list[DropFact], exported_at: datetime
) -> list[tuple[ExportedEventsEntry, EventsPosition]]:
    facts = _events_facts()
    rows: list[tuple[ExportedEventsEntry, EventsPosition]] = []
    for held in derivations:
        marker = held.marker
        position = EventsPosition(marker.derived_at, marker.segment_id, marker.extractor_version)
        rows += [(row, position) for row in derivation_rows(facts, held, _PATHS, exported_at)]
    for drop in drops:
        rows.append((dropped_row(facts, drop, exported_at), EventsPosition(drop.dropped_at, drop.segment_id)))
    return rows


def _write_events_pass(
    writer: DirectoryEgressWriter,
    started_at: datetime,
    version: str,
    rows: list[tuple[ExportedEventsEntry, EventsPosition]],
) -> None:
    egress_pass = EgressPass(started_at, extractor_version=version)
    placed: list[PlacedFile] = []
    for day in sorted({row.step_started_at.astimezone(UTC).date() for row, _ in rows}):
        values: list[EgressValues] = [
            events_egress_row(row, position)
            for row, position in rows
            if row.step_started_at.astimezone(UTC).date() == day
        ]
        written = writer.write(EgressBatch(EVENTS_SCHEMA, day, egress_pass, values))
        assert not hasattr(written, "cause"), written
        placed += written.files  # type: ignore[union-attr]
    committed = writer.commit_pass(egress_pass, placed)
    assert not hasattr(committed, "cause"), committed


def _write_pass(
    writer: DirectoryEgressWriter,
    started_at: datetime,
    steps: list[tuple[ExportedStep, CursorKey]],
    invs: list[ExportedInvocation],
) -> None:
    egress_pass = EgressPass(started_at)
    placed: list[PlacedFile] = []
    batches = (
        (STEPS_SCHEMA, [(row.ended_at, step_egress_row(row, key)) for row, key in steps]),
        (INVOCATIONS_SCHEMA, [(row.recorded_at, invocation_egress_row(row)) for row in invs]),
    )
    for schema, timed in batches:
        for day in sorted({at.astimezone(UTC).date() for at, _ in timed}):
            rows: list[EgressValues] = [row for at, row in timed if at.astimezone(UTC).date() == day]
            written = writer.write(EgressBatch(schema, day, egress_pass, rows))
            assert not hasattr(written, "cause"), written
            placed += written.files  # type: ignore[union-attr]
    committed = writer.commit_pass(egress_pass, placed)
    assert not hasattr(committed, "cause"), committed


def _export(directory: Path, encoder: Any) -> None:
    writer = DirectoryEgressWriter(directory, _SETTINGS, _TOKEN, encoder)
    steps, invocations = _scenario_rows()
    _write_pass(writer, _FIRST_PASS, steps, invocations)
    more_steps, more_invocations = _rewritten(steps, invocations)
    _write_pass(writer, _SECOND_PASS, more_steps, more_invocations)
    _write_events_pass(writer, _FIRST_PASS, _OLD_VERSION, _events_pass_rows(_EVENTS_FIRST, [], _FIRST_PASS))
    _write_events_pass(
        writer, _SECOND_PASS, _NEW_VERSION, _events_pass_rows(_EVENTS_SECOND, _EVENTS_SECOND_DROPS, _SECOND_PASS)
    )


def _normalized_tree(directory: Path) -> dict[str, str]:
    """The export as readable text by export-relative path: decompressed NDJSON and manifests without digests. The
    ``_schema`` documents are compared separately and left out."""
    tree: dict[str, str] = {}
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        relative = path.relative_to(directory)
        if relative.parts[0] in {".staging", "_schema"}:
            continue
        if path.name.endswith(".ndjson.gz"):
            tree[relative.as_posix().removesuffix(".gz")] = gzip.decompress(path.read_bytes()).decode()
        elif relative.parts[0] == "_manifests":
            document = json.loads(path.read_text())
            for entry in document["files"]:
                entry["path"] = entry["path"].removesuffix(".gz")
                entry["sha256"] = "<normalized>"
            tree[relative.as_posix()] = json.dumps(document, indent=2) + "\n"
    return tree


def _golden_tree() -> dict[str, str]:
    return {p.relative_to(_GOLDEN_DIR).as_posix(): p.read_text() for p in sorted(_GOLDEN_DIR.rglob("*")) if p.is_file()}


# -- regeneration ----------------------------------------------------------------------------------------------------


def _table(header: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(line[i]) for line in [header, *rows]) for i in range(len(header))]

    def render(line: list[str]) -> str:
        return "| " + " | ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=True)) + " |"

    return "\n".join([render(header), "| " + " | ".join("-" * w for w in widths) + " |", *map(render, rows)])


def _values_cell(column: dict[str, Any]) -> str:
    values = column.get("values")
    if values is None:
        return ""
    listed = ", ".join(f"`{v}`" for v in values["list"])
    return f"closed: {listed}" if values["closed"] else f"open: {listed}"


def _on_cell(column: dict[str, Any]) -> str:
    on = column["on"]
    return "all" if on == _RECORD_TYPES else ", ".join(on)


def _render_dictionary() -> str:
    sections: list[str] = []
    for dataset in _dictionary()["datasets"]:
        name = dataset["name"]
        has_on = any("on" in c for c in dataset["columns"])
        rows = [
            [
                f"`{c['name']}`",
                f"`{c['type']}`",
                *([_on_cell(c)] if has_on else []),
                "yes" if c["nullable"] else "no",
                c["meaning"],
                _values_cell(c),
            ]
            for c in dataset["columns"]
        ]
        header = ["Column", "Type", *(["On"] if has_on else []), "Null", "Meaning", "Values"]
        identity = ", ".join(f"`{c}`" for c in dataset["identity"])
        noun = "identity columns" if len(dataset["identity"]) > 1 else "identity column"
        sections.append(
            f"### `{name}`\n\n"
            f"Major version {dataset['major_version']}; {noun} {identity}; partitioned by the "
            f"UTC date of `{dataset['partition']}`.\n\n{_table(header, rows)}\n\n"
            + "\n\n".join(f"View `{view}`:\n\n```sql\n{_view_sql(view)}\n```" for view in dataset["views"])
        )
    return "\n\n".join(sections)


def _doc_block(text: str) -> str:
    return text.split(_BEGIN + "\n\n", 1)[1].split(_END, 1)[0].removesuffix("\n\n")


def _regenerate() -> None:
    schemas = _contract_schemas()
    _SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for name, schema in schemas.items():
        (_SCHEMA_DIR / f"{name}.v{schema.major_version}.json").write_bytes(schema_document(schema))
    for stale in sorted(_GOLDEN_DIR.rglob("*"), reverse=True) if _GOLDEN_DIR.exists() else []:
        stale.unlink() if stale.is_file() else stale.rmdir()
    import tempfile

    with tempfile.TemporaryDirectory() as scratch:
        _export(Path(scratch), NdjsonEncoder())
        for relative, text in _normalized_tree(Path(scratch)).items():
            target = _GOLDEN_DIR / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
    if _DOC.exists():
        text = _DOC.read_text()
        head, rest = text.split(_BEGIN + "\n\n", 1)
        tail = rest.split(_END, 1)[1]
        _DOC.write_text(f"{head}{_BEGIN}\n\n{_render_dictionary()}\n\n{_END}{tail}")


@pytest.fixture(scope="module", autouse=True)
def _regenerate_if_asked() -> None:
    if os.environ.get(_REGEN_VARIABLE) == "1":
        _regenerate()


@pytest.fixture(scope="module")
def ndjson_export(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("ndjson")
    _export(directory, NdjsonEncoder())
    return directory


@pytest.fixture(scope="module")
def parquet_export(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("parquet")
    _export(directory, ParquetEncoder())
    return directory


# -- the code is bound to the contract -------------------------------------------------------------------------------


def test_the_contract_publishes_exactly_the_three_datasets() -> None:
    assert list(_datasets()) == _DATASET_NAMES


@pytest.mark.parametrize("name", _DATASET_NAMES)
def test_the_code_schema_is_the_contract(name: str) -> None:
    contract = _contract_schemas()[name]
    code = _CODE_SCHEMAS[name]
    assert [c.name for c in contract.columns] == [c.name for c in code.columns], f"column order; {_REGEN_COMMAND}"
    for published, built in zip(contract.columns, code.columns, strict=True):
        assert published == built, f"{name}.{published.name} differs from hub/domain/observability/egress/schema.py"
    assert contract.major_version == code.major_version


@pytest.mark.parametrize("name", _DATASET_NAMES)
def test_identity_and_partition_columns_are_columns(name: str) -> None:
    dataset = _datasets()[name]
    by_name = {c["name"]: c for c in dataset["columns"]}
    assert {*dataset["identity"], dataset["partition"]} <= set(by_name)
    if len(dataset["identity"]) == 1:
        assert by_name[dataset["identity"][0]]["nullable"] is False


def test_every_declared_view_is_a_file_and_every_view_file_is_declared() -> None:
    declared = {view for _, view in _views()}
    assert declared == {p.stem for p in _CONTRACT_DIR.glob("*.sql")}
    assert [view for name, view in _views() if name != "events"] == ["steps_newest", "invocations_newest"]


def test_events_columns_name_the_record_types_they_appear_on() -> None:
    for column in _datasets()["events"]["columns"]:
        assert set(column["on"]) <= set(_RECORD_TYPES), column["name"]
        assert column["on"], column["name"]
        if not column["nullable"]:
            assert set(column["on"]) == set(_RECORD_TYPES), f"{column['name']} is not null yet not on every record"


def test_the_events_kind_column_is_open_and_lists_the_known_kinds() -> None:
    kinds = {c["name"]: c for c in _datasets()["events"]["columns"]}["kind"]["values"]
    assert kinds["closed"] is False
    assert {"file_read", "skill_invocation", "agent_spawn"} == set(kinds["list"])


def test_closed_enumerations_are_the_code_enums() -> None:
    closed = {
        (name, c["name"]): set(c["values"]["list"])
        for name, d in _datasets().items()
        for c in d["columns"]
        if c.get("values", {}).get("closed")
    }
    assert closed == _CODE_ENUMS


def test_the_scenario_covers_every_closed_value() -> None:
    steps, _ = _scenario_rows()
    for (dataset, column), values in _CODE_ENUMS.items():
        if dataset != "steps":
            continue
        seen = {getattr(row, column) for row, _ in steps} - {None}
        assert seen <= values
    assert {row.step_kind for row, _ in steps} == _CODE_ENUMS[("steps", "step_kind")]
    assert {row.outcome for row, _ in steps} == _CODE_ENUMS[("steps", "outcome")]


def test_the_events_scenario_covers_every_record_type_and_kind() -> None:
    rows = [row for row, _ in _events_pass_rows(_EVENTS_FIRST + _EVENTS_SECOND, list(_DROPS.values()), _FIRST_PASS)]
    assert {row.record_type for row in rows} == set(_RECORD_TYPES)
    kinds = {c["name"]: c for c in _datasets()["events"]["columns"]}["kind"]["values"]["list"]
    assert {row.kind for row in rows if row.kind} == set(kinds)


def test_the_row_fields_are_the_contract_columns() -> None:
    assert [f.name for f in fields(ExportedStep)] == [c["name"] for c in _datasets()["steps"]["columns"]]
    assert [f.name for f in fields(ExportedInvocation)] == [c["name"] for c in _datasets()["invocations"]["columns"]]
    assert [f.name for f in fields(ExportedEventsEntry)] == [c["name"] for c in _datasets()["events"]["columns"]]


# -- _schema ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", _DATASET_NAMES)
def test_the_committed_schema_is_the_contract_rendered(name: str) -> None:
    schema = _contract_schemas()[name]
    committed = (_SCHEMA_DIR / f"{name}.v{schema.major_version}.json").read_bytes()
    assert committed == schema_document(schema), f"stale _schema; {_REGEN_COMMAND}"


@pytest.mark.parametrize("name", _DATASET_NAMES)
@pytest.mark.parametrize("export", ["ndjson_export", "parquet_export"])
def test_the_writer_places_the_committed_schema(name: str, export: str, request: pytest.FixtureRequest) -> None:
    directory: Path = request.getfixturevalue(export)
    placed = (directory / "_schema" / f"{name}.v1.json").read_bytes()
    assert placed == (_SCHEMA_DIR / f"{name}.v1.json").read_bytes()


# -- the golden ------------------------------------------------------------------------------------------------------


def test_the_ndjson_export_is_the_golden(ndjson_export: Path) -> None:
    live = _normalized_tree(ndjson_export)
    golden = _golden_tree()
    assert sorted(live) == sorted(golden), f"golden file set drifted; {_REGEN_COMMAND}"
    for path, text in live.items():
        assert text == golden[path], f"{path} drifted from the golden; {_REGEN_COMMAND}"


def test_the_golden_holds_a_row_exported_twice() -> None:
    for dataset in _datasets().values():
        seen: dict[tuple[object, ...], int] = {}
        for path, text in _golden_tree().items():
            if path.startswith(f"{dataset['name']}/"):
                for row in map(json.loads, text.splitlines()):
                    if row.get("record_type", RECORD_EVENT) == RECORD_EVENT:
                        key = tuple(row[c] for c in dataset["identity"])
                        seen[key] = seen.get(key, 0) + 1
        assert any(count > 1 for count in seen.values()), dataset["name"]


def test_events_rows_fill_exactly_the_columns_their_record_type_appears_on() -> None:
    on = {c["name"]: set(c["on"]) for c in _datasets()["events"]["columns"]}
    seen: set[str] = set()
    for path, text in _golden_tree().items():
        if path.startswith("events/"):
            for row in map(json.loads, text.splitlines()):
                seen.add(row["record_type"])
                filled = {name for name, value in row.items() if value is not None}
                assert filled <= {n for n, types in on.items() if row["record_type"] in types}, row
    assert seen == set(_RECORD_TYPES)


def _wire(schema: DatasetSchema, row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for column in schema.columns:
        value = row[column.name]
        if isinstance(value, datetime):
            value = rfc3339_utc(value)
        elif isinstance(value, Decimal):
            value = format(value, f".{MONEY_SCALE}f")
        out[column.name] = value
    return out


def test_the_parquet_export_decodes_to_the_golden_rows(parquet_export: Path) -> None:
    schemas = _contract_schemas()
    for name, schema in schemas.items():
        decoded: list[dict[str, Any]] = []
        for path in sorted((parquet_export / name).rglob("*.parquet")):
            decoded += [_wire(schema, row) for row in pq.read_table(path).to_pylist()]
        golden: list[dict[str, Any]] = []
        for path, text in sorted(_golden_tree().items()):
            if path.startswith(f"{name}/"):
                golden += [json.loads(line) for line in text.splitlines()]
        assert decoded == golden, f"{name} parquet rows differ from the golden; {_REGEN_COMMAND}"


def test_the_parquet_schema_is_the_contract_types(parquet_export: Path) -> None:
    for name, schema in _contract_schemas().items():
        expected = arrow_schema(schema)
        for path in sorted((parquet_export / name).rglob("*.parquet")):
            assert pq.read_schema(path).equals(expected), path.name
        assert [f.name for f in expected] == [c.name for c in schema.columns]


# -- the published page ----------------------------------------------------------------------------------------------


def test_the_published_dictionary_is_the_contract_rendered() -> None:
    assert _doc_block(_DOC.read_text()) == _render_dictionary(), f"stale dictionary block; {_REGEN_COMMAND}"


def _published_views() -> dict[str, str]:
    block = _doc_block(_DOC.read_text())
    sql = re.findall(r"```sql\n(.*?)\n```", block, re.DOTALL)
    return dict(zip([view for _, view in _views()], sql, strict=True))


@pytest.mark.parametrize("name", ["steps", "invocations"])
def test_the_published_newest_copy_view_returns_one_latest_row_per_identity(name: str) -> None:
    dataset = _datasets()[name]
    (identity,) = dataset["identity"]
    files = sorted(str(p) for p in (_GOLDEN_DIR / name).rglob("*.ndjson"))
    assert files
    connection = duckdb.connect()
    connection.execute("SET TimeZone = 'UTC'")
    listed = ", ".join(f"'{f}'" for f in files)
    connection.execute(f"CREATE VIEW {name} AS SELECT * FROM read_json_auto([{listed}], format = 'newline_delimited')")
    view = _published_views()[f"{name}_newest"]
    got = connection.execute(
        f"SELECT {identity}, strftime(CAST(exported_at AS TIMESTAMP), '%Y-%m-%dT%H:%M:%SZ') FROM ({view})"
    ).fetchall()
    latest: dict[object, str] = {}
    for path in files:
        for line in Path(path).read_text().splitlines():
            row = json.loads(line)
            latest[row[identity]] = max(latest.get(row[identity], ""), row["exported_at"][:19] + "Z")
    assert len(got) == len(latest)
    assert dict(got) == latest


def _golden_steps_connection() -> duckdb.DuckDBPyConnection:
    files = sorted(str(p) for p in (_GOLDEN_DIR / "steps").rglob("*.ndjson"))
    connection = duckdb.connect()
    connection.execute("SET TimeZone = 'UTC'")
    listed = ", ".join(f"'{f}'" for f in files)
    connection.execute(f"CREATE VIEW steps AS SELECT * FROM read_json_auto([{listed}], format = 'newline_delimited')")
    connection.execute(f"CREATE VIEW steps_newest AS {_published_views()['steps_newest']}")
    return connection


def test_the_cost_recipe_groups_the_newest_copies_by_station_and_day() -> None:
    connection = _golden_steps_connection()
    got = connection.execute(recipe(COST_BY_NODE_BY_DAY)).fetchall()
    expected = connection.execute(
        "SELECT graph_name, node_name, CAST(ended_at AS DATE), sum(CAST(cost_billed_usd AS DECIMAL(18, 9))), "
        "sum(CAST(cost_estimated_usd AS DECIMAL(18, 9))) "
        "FROM steps_newest GROUP BY ALL ORDER BY 3, 1, 2"
    ).fetchall()
    assert got == expected
    assert {(g, n) for g, n, *_ in got} >= {("flow", "build"), ("flow", "poll"), ("legacy-flow", "build")}
    assert sum(r[3] for r in got if r[3] is not None) > 0


def test_the_slowest_recipe_names_the_station_with_the_highest_mean_duration() -> None:
    connection = _golden_steps_connection()
    sql = recipe(SLOWEST_STATION_OF_THE_WEEK).replace("now()", "TIMESTAMP '2026-01-05 00:00:00'")
    assert connection.execute(sql).fetchall() == [("flow", "poll", pytest.approx(93000.0))]


def test_the_published_view_is_the_authored_view() -> None:
    for view, published in _published_views().items():
        assert published == _view_sql(view)


def test_versioning_names_the_egress_contract() -> None:
    text = _VERSIONING_DOC.read_text()
    assert re.search(r"egress contract", text, re.IGNORECASE)
    assert "contracts/egress" in text


# -- the events views over the golden --------------------------------------------------------------------------------


def _golden_events_connection() -> duckdb.DuckDBPyConnection:
    files = sorted(str(p) for p in (_GOLDEN_DIR / "events").rglob("*.ndjson"))
    assert files
    connection = duckdb.connect()
    connection.execute("SET TimeZone = 'UTC'")
    listed = ", ".join(f"'{f}'" for f in files)
    connection.execute(
        f"CREATE VIEW events AS SELECT * FROM read_json_auto([{listed}], format = 'newline_delimited', "
        "union_by_name = true, sample_size = -1)"
    )
    for view in _datasets()["events"]["views"]:
        connection.execute(f"CREATE VIEW {view} AS {_published_views()[view]}")
    return connection


def test_events_current_counts_each_segment_by_its_newest_derivation_unless_dropped_after() -> None:
    connection = _golden_events_connection()
    got: dict[str, list[tuple[str, str]]] = {}
    for segment, kind, subject in connection.execute(
        "SELECT segment_id, kind, subject FROM events_current ORDER BY segment_id, kind, subject"
    ).fetchall():
        got.setdefault(segment, []).append((kind, subject))
    assert got == {
        # upgraded from the old version: only the new version's event, the old version's two no longer count
        "seg-upgraded": [("file_read", "new.py")],
        # still only under the old version: it keeps counting through the upgrade
        "seg-old-only": [("file_read", "kept.py")],
        # re-derived to an empty derivation: its older event stops counting and nothing replaces it
        # (absent: "seg-emptied")
        # dropped after its derivation: nothing counts (absent: "seg-dropped")
        # dropped, then derived again: only the derivation newer than the drop counts
        "seg-revived": [("file_read", "after.py")],
        # one derivation exported twice: counted once
        "seg-twice": [("file_read", "once.py")],
        # two kinds at one turn_path and occurrence: both count
        "seg-kinds": [("agent_spawn", "reviewer"), ("file_read", "both.py")],
        # an event at depth two counts like any other
        "seg-sidechain": [("file_read", "deep.py"), ("file_read", "main.py")],
        # re-derived under the same version: the later derivation alone
        "seg-rederived": [("file_read", "second.py")],
    }


def test_events_current_returns_the_event_columns_of_each_counting_row() -> None:
    connection = _golden_events_connection()
    [(depth, agent_type, node_name)] = connection.execute(
        "SELECT depth, agent_type, node_name FROM events_current WHERE subject = 'deep.py'"
    ).fetchall()
    assert (depth, agent_type, node_name) == (2, "reviewer", "build")
    columns = [d[0] for d in connection.execute("SELECT * FROM events_current").description]
    assert columns[: len(_datasets()["events"]["columns"])] == [c["name"] for c in _datasets()["events"]["columns"]]


def test_events_by_version_holds_the_newest_derivation_of_each_segment_under_each_version() -> None:
    connection = _golden_events_connection()
    got: dict[tuple[str, str], list[str]] = {}
    for segment, version, subject in connection.execute(
        "SELECT segment_id, extractor_version, subject FROM events_by_version ORDER BY 1, 2, 3"
    ).fetchall():
        got.setdefault((segment, version), []).append(subject)
    assert got == {
        # both versions of an upgraded segment are there, each with its own newest derivation
        ("seg-upgraded", _OLD_VERSION): ["lint", "old.py"],
        ("seg-upgraded", _NEW_VERSION): ["new.py"],
        ("seg-old-only", _OLD_VERSION): ["kept.py"],
        # a drop voids every version of the segment; the old derivation of a revived one predates its drop
        ("seg-revived", _NEW_VERSION): ["after.py"],
        ("seg-twice", _OLD_VERSION): ["once.py"],
        ("seg-kinds", _OLD_VERSION): ["both.py", "reviewer"],
        ("seg-sidechain", _OLD_VERSION): ["deep.py", "main.py"],
        ("seg-rederived", _NEW_VERSION): ["second.py"],
        # an empty new derivation does not void the old version's own newest derivation
        ("seg-emptied", _OLD_VERSION): ["gone.py"],
    }
