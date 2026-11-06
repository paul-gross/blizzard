"""The OpenCode 2 proof's offline halves: version admission, report completeness and classification, the sanitized
corpus it writes, the store snapshot, and the diagnostic's dispatch by major version."""

from __future__ import annotations

import json
import sqlite3
import stat
from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.runner.cli import runner as runner_group
from blizzard.runner.harness.admission import classification_of_manifest
from blizzard.runner.harness.compatibility import CompatibilityClassification, EvidenceState
from blizzard.runner.harness.json_value import JsonObject
from blizzard.runner.harness.opencode_v2.compatibility.corpus import ProofEvidence, Sanitizer
from blizzard.runner.harness.opencode_v2.compatibility.proof import final_assistant_text, part_error_type, tool_output
from blizzard.runner.harness.opencode_v2.compatibility.rows import (
    ROW_ROSTER,
    IncompleteProofError,
    ProofReport,
    ProofRow,
    RowObservation,
    classify_row,
)
from blizzard.runner.harness.opencode_v2.compatibility.store_migration import (
    MIGRATION_KV_KEY,
    first_session_id,
    snapshot,
)
from blizzard.runner.harness.opencode_v2.version import normalize_opencode_v2_version


def _observed(row: ProofRow) -> RowObservation:
    return RowObservation(row, EvidenceState.OBSERVED, f"{row.value} held", (row.value,))


def _report(*overrides: RowObservation, admitted: bool = True) -> ProofReport:
    replaced = {observation.row: observation for observation in overrides}
    rows = [replaced.get(row, _observed(row)) for row in ROW_ROSTER]
    return ProofReport.complete("2.1.4", ">=2.0,<3.0", admitted, rows)


@pytest.mark.parametrize(
    ("raw", "version"),
    [("opencode v2.1.4\n", "2.1.4"), ("2.1.4", "2.1.4"), ("v2.0.0", "2.0.0"), ("two\nlines", None), (None, None)],
)
def test_the_v2_version_normalizer_reads_one_line(raw: str | None, version: str | None) -> None:
    assert normalize_opencode_v2_version(raw) == version


def test_the_roster_covers_every_contract_row_once() -> None:
    assert len(ROW_ROSTER) == len(set(ROW_ROSTER)) == 23


def test_a_report_missing_a_row_is_refused() -> None:
    with pytest.raises(IncompleteProofError, match="store_migration"):
        ProofReport.complete("2.1.4", ">=2.0", True, [_observed(row) for row in ROW_ROSTER[:-1]])


def test_a_report_recording_a_row_twice_is_refused() -> None:
    with pytest.raises(IncompleteProofError, match="twice"):
        ProofReport.complete("2.1.4", ">=2.0", True, [*map(_observed, ROW_ROSTER), _observed(ProofRow.VERSION)])


@pytest.mark.parametrize(
    ("observation", "classification"),
    [
        (RowObservation(ProofRow.PRICING, EvidenceState.ABSENT, "none"), CompatibilityClassification.DEGRADED),
        (RowObservation(ProofRow.EVENTS, EvidenceState.ABSENT, "none"), CompatibilityClassification.DEGRADED),
        (RowObservation(ProofRow.INTERRUPT, EvidenceState.ABSENT, "none"), CompatibilityClassification.BLOCKING),
        (RowObservation(ProofRow.PRICING, EvidenceState.FAILED, "boom"), CompatibilityClassification.BLOCKING),
        (RowObservation(ProofRow.TAKEOVER, EvidenceState.AMBIGUOUS, "?"), CompatibilityClassification.BLOCKING),
    ],
)
def test_only_pricing_and_events_degrade_and_only_when_absent(
    observation: RowObservation, classification: CompatibilityClassification
) -> None:
    assert classify_row(observation) is classification


def test_the_report_takes_its_worst_row_and_blocks_an_unadmitted_version() -> None:
    assert _report().classification() is CompatibilityClassification.SUPPORTED
    pricing_absent = RowObservation(ProofRow.PRICING, EvidenceState.ABSENT, "none")
    assert _report(pricing_absent).classification() is CompatibilityClassification.DEGRADED
    failed = RowObservation(ProofRow.SERVICE_RESTART, EvidenceState.FAILED, "x")
    assert _report(pricing_absent, failed).classification() is CompatibilityClassification.BLOCKING
    assert not _report(admitted=False).admissible()


def test_the_sanitizer_redacts_secrets_credential_keys_and_host_paths() -> None:
    sanitizer = Sanitizer(secrets=("pw-123",), replacements=(("/home/op", "<home>"), ("/home/op/scratch", "<scratch>")))
    value: JsonObject = {
        "url": "http://opencode:pw-123@127.0.0.1",
        "location": {"directory": "/home/op/scratch/repo"},
        "settings": {"apiKey": "sk-live"},
        "nested": [{"apiKey": "sk-2", "path": "/home/op/.config"}],
    }
    assert sanitizer.value(value) == {
        "url": "http://opencode:<redacted>@127.0.0.1",
        "location": {"directory": "<scratch>/repo"},
        "settings": "<redacted>",
        "nested": [{"apiKey": "<redacted>", "path": "<home>/.config"}],
    }


def test_the_evidence_writes_fixtures_a_manifest_and_routes_the_store_fixture_to_opencode1(tmp_path: Path) -> None:
    evidence = ProofEvidence(tmp_path / "out", Sanitizer(secrets=("pw",), replacements=()))
    captures: JsonObject = {"events": [{"pw": "pw"}], "error_shapes_from_source": {}, "store_migration": {"signal": 1}}
    evidence.write(_report(), captures, model="opencode/pulse#high")
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert manifest["major_version"] == 2
    assert manifest["version"] == "2.1.4"
    assert classification_of_manifest(manifest) is CompatibilityClassification.SUPPORTED
    assert [row["name"] for row in manifest["rows"]] == [row.value for row in ROW_ROSTER]
    assert {d["row"] for d in manifest["declared_degradations"]} == {"permission_rules", "session_environment"}
    assert manifest["fixtures"] == [
        {"path": "error_shapes_from_source.json", "source": "source"},
        {"path": "events.json", "source": "live"},
    ]
    assert (tmp_path / "out" / "opencode1" / "store_migration.json").is_file()
    assert not (tmp_path / "out" / "store_migration.json").exists()
    assert "pw" not in (tmp_path / "out" / "events.json").read_text().replace("<redacted>", "")


def test_the_store_snapshot_reads_schema_counts_and_the_migration_signal(tmp_path: Path) -> None:
    path = tmp_path / "opencode.db"
    with sqlite3.connect(path) as db:
        db.execute("create table session (id text primary key, title text)")
        db.execute("insert into session values ('ses_1', 't')")
        db.execute("create table kv (key text primary key, value text)")
        db.execute("insert into kv values (?, ?)", (MIGRATION_KV_KEY, json.dumps({"phase": "completed"})))
    taken = snapshot(path)
    assert taken["counts"] == {"kv": 1, "session": 1}
    assert taken["kv"] == {MIGRATION_KV_KEY: {"phase": "completed"}}
    assert first_session_id(taken) == "ses_1"


def test_part_readers_take_the_tool_output_error_type_and_final_text() -> None:
    part: JsonObject = {
        "type": "tool",
        "state": {"content": [{"type": "text", "text": "out"}], "error": {"type": "x.y"}},
    }
    assert tool_output(part) == "out"
    assert part_error_type(part) == "x.y"
    messages: list[JsonObject] = [
        {"type": "assistant", "content": [{"type": "text", "text": "first"}]},
        {"type": "assistant", "content": [{"type": "reasoning", "text": "r"}, {"type": "text", "text": "last"}]},
    ]
    assert final_assistant_text(messages) == "last"


def _binary(tmp_path: Path, version_line: str) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "fake-opencode"
    path.write_text(f"#!/bin/sh\necho '{version_line}'\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    return str(path)


@pytest.mark.parametrize("version_line", ["opencode v3.0.0", "1.17.0", "not a version"])
def test_a_version_neither_major_admits_is_reported_and_nothing_is_probed(tmp_path: Path, version_line: str) -> None:
    evidence = tmp_path / "evidence"
    result = CliRunner().invoke(
        runner_group,
        [
            "opencode",
            "compatibility",
            "--binary",
            _binary(tmp_path / "bin", version_line),
            "--model",
            "opencode/m",
            "--variant",
            "high",
            "--evidence-dir",
            str(evidence),
            "--live-provider",
        ],
    )
    assert result.exit_code == 1
    assert "outside every admitted range" in result.output
    assert "nothing was probed" in result.output
    assert not evidence.exists()
