"""Hermetic unit contract for the pinned OpenCode compatibility proof.

The corpus is sanitized JSON only.  These tests never launch OpenCode, contact a provider, or
read a credential file: they pin the external shapes, policy closure, sanitizer, and identity
cursor that a later live diagnostic will consume.
"""

from __future__ import annotations

import copy
import gzip
import io
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from blizzard.runner.harness.compatibility import (
    BLOCKING,
    DEGRADED,
    PROBE_ROSTER,
    REQUIRED_PROBES,
    SUPPORTED,
    CompatibilityClassification,
    CompatibilityContractError,
    CompatibilityDiagnostic,
    CompatibilityProbe,
    CompatibilityReport,
    EvidenceState,
    IncompleteProbeReportError,
    ProbeObservation,
    classify_observation,
)
from blizzard.runner.harness.internal.opencode_attach import (
    OpenCodeAttachProxy,
    OpenCodeAttachRequest,
    OpenCodeAttachSignal,
)
from blizzard.runner.harness.internal.opencode_cursor import (
    CursorError,
    CursorRecord,
    MessagePartCursor,
    records_for_export,
)
from blizzard.runner.harness.internal.opencode_evidence import OpenCodeEvidence
from blizzard.runner.harness.internal.opencode_facts import has_exact_permission_denial
from blizzard.runner.harness.internal.opencode_loopback import (
    LoopbackRequest,
    LoopbackResponse,
    LoopbackTransportError,
    UrllibLoopbackTransport,
)
from blizzard.runner.harness.internal.opencode_probe import PINNED_OPENCODE_VERSION
from blizzard.runner.harness.internal.opencode_sanitizer import REDACTED, sanitize_json, sanitize_value
from blizzard.runner.harness.internal.opencode_shapes import (
    OpenCodeRunEvent,
    OpenCodeShapeError,
    UnknownOpenCodeShapeError,
    parse_child_sessions,
    parse_model_reference,
    parse_run_event,
    parse_run_events,
    parse_run_jsonl,
    parse_session_export,
    parse_worker_config,
)
from blizzard.runner.harness.internal.opencode_transcript import TranscriptExportSample, inspect_transcript

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORPUS_DIR = _REPO_ROOT / "contracts" / "opencode" / PINNED_OPENCODE_VERSION


def _manifest() -> dict[str, Any]:
    return json.loads((_CORPUS_DIR / "manifest.json").read_text())


def _fixtures() -> list[tuple[str, dict[str, Any]]]:
    manifest = _manifest()
    return [(entry["name"], json.loads((_CORPUS_DIR / entry["path"]).read_text())) for entry in manifest["fixtures"]]


def _all_observations(state: EvidenceState | str = EvidenceState.OBSERVED) -> list[ProbeObservation]:
    return [
        ProbeObservation(probe, state, f"observed {probe.value}", (f"fixtures/{probe.value}",))
        for probe in PROBE_ROSTER
    ]


def test_probe_roster_is_closed_and_ordered() -> None:
    assert [probe.value for probe in PROBE_ROSTER] == [
        "fresh_turn",
        "resume",
        "process_control",
        "judgement",
        "root_hook",
        "permission",
        "model_variant",
        "usage_cost",
        "takeover",
        "transcript_read",
        "transcript_cursor",
        "child_sessions",
        "configuration_isolation",
    ]
    assert len(set(PROBE_ROSTER)) == len(PROBE_ROSTER)


@pytest.mark.parametrize(
    ("probe", "state", "expected"),
    [
        (CompatibilityProbe.FRESH_TURN, EvidenceState.OBSERVED, SUPPORTED),
        (CompatibilityProbe.ROOT_HOOK, EvidenceState.ABSENT, DEGRADED),
        (CompatibilityProbe.USAGE_COST, EvidenceState.ABSENT, DEGRADED),
        (CompatibilityProbe.CHILD_SESSIONS, EvidenceState.ABSENT, DEGRADED),
        (CompatibilityProbe.TRANSCRIPT_READ, EvidenceState.ABSENT, BLOCKING),
        (CompatibilityProbe.PROCESS_CONTROL, EvidenceState.FAILED, BLOCKING),
        (CompatibilityProbe.TRANSCRIPT_CURSOR, EvidenceState.AMBIGUOUS, BLOCKING),
    ],
)
def test_classification_policy_is_deterministic(
    probe: CompatibilityProbe, state: EvidenceState, expected: CompatibilityClassification
) -> None:
    observation = ProbeObservation(probe, state, "fixture observation")
    assert classify_observation(observation) is expected


def test_complete_report_has_every_probe_in_roster_order() -> None:
    report = CompatibilityReport.from_observations(
        PINNED_OPENCODE_VERSION, PINNED_OPENCODE_VERSION, _all_observations()
    )

    assert report.complete is True
    assert report.classification is SUPPORTED
    assert report.admissible is True
    assert tuple(result.probe for result in report.results) == PROBE_ROSTER
    assert report.to_payload()["classification"] == "supported"


def test_diagnostic_requires_the_probe_to_declare_its_observed_version() -> None:
    class ProbeWithoutVersion:
        expected_version = PINNED_OPENCODE_VERSION

        def run(self) -> list[ProbeObservation]:
            return _all_observations()

    with pytest.raises(CompatibilityContractError, match="did not report both versions"):
        CompatibilityDiagnostic(ProbeWithoutVersion()).run()  # pyright: ignore[reportArgumentType]


def test_report_rejects_a_missing_probe_instead_of_publishing_an_incomplete_result() -> None:
    with pytest.raises(IncompleteProbeReportError, match="missing probes"):
        CompatibilityReport.from_observations(
            PINNED_OPENCODE_VERSION, PINNED_OPENCODE_VERSION, _all_observations()[:-1]
        )


def test_report_rejects_a_duplicate_probe() -> None:
    observations = [*_all_observations(), ProbeObservation.observed(CompatibilityProbe.FRESH_TURN, "again")]

    with pytest.raises(IncompleteProbeReportError, match="duplicate"):
        CompatibilityReport.from_observations(PINNED_OPENCODE_VERSION, PINNED_OPENCODE_VERSION, observations)


def test_a_version_mismatch_is_blocking_even_with_successful_probe_evidence() -> None:
    report = CompatibilityReport.from_observations("1.18.24", PINNED_OPENCODE_VERSION, _all_observations())

    assert report.version_matches_pin is False
    assert report.classification is BLOCKING
    assert report.admissible is False
    assert report.blocking_reasons == ("observed '1.18.24', expected '1.18.25'",)


def test_corpus_manifest_closes_categories_and_parser_shape_coverage() -> None:
    manifest = _manifest()
    expected_categories = {
        "success",
        "provider_error",
        "permission_denial",
        "interrupted_tool",
        "compaction",
        "child_session",
    }
    assert manifest["harness"] == "opencode"
    assert manifest["version"] == PINNED_OPENCODE_VERSION
    assert manifest["sanitized"] is True
    assert tuple(manifest["required_probes"]) == REQUIRED_PROBES
    assert {entry["category"] for entry in manifest["fixtures"]} == expected_categories
    assert set(manifest["categories"]) == expected_categories
    fixture_names = {entry["name"] for entry in manifest["fixtures"]}
    assert len(fixture_names) == len(manifest["fixtures"])
    live_evidence_paths = {entry["path"] for entry in manifest["live_evidence"]["fixtures"].values() if "path" in entry}
    assert {entry["path"] for entry in manifest["fixtures"]} == {
        path.name
        for path in _CORPUS_DIR.glob("*.json")
        if path.name != "manifest.json" and path.name not in live_evidence_paths
    }
    assert set(manifest["probe_evidence"]) == set(REQUIRED_PROBES)
    for fixtures in manifest["probe_evidence"].values():
        assert fixtures
        assert set(fixtures) <= fixture_names
    assert {
        "run_event",
        "session_export",
        "provider_error",
        "permission_request",
        "tool_state",
        "compaction_part",
        "token_usage",
        "child_sessions",
        "child_session_export",
        "worker_config",
        "model_reference",
    } <= set(manifest["shape_parsers"])
    for shape in manifest["shape_parsers"].values():
        assert shape["fixtures"]
        assert set(shape["fixtures"]) <= fixture_names


def test_manifest_retains_sanitized_live_permission_evidence() -> None:
    manifest = _manifest()
    live = manifest["live_evidence"]
    permission_path = _CORPUS_DIR / live["fixtures"]["permission_denial"]["path"]
    payload = json.loads(permission_path.read_text())

    events = parse_run_events(payload["events"])
    config = parse_worker_config(payload["config"])
    model = parse_model_reference(payload["model"])
    assert any(
        event.part is not None
        and event.part.tool == "bash"
        and event.part.state is not None
        and event.part.state.status == "error"
        for event in events
    )
    assert config.permissions["bash"] == {"*": "deny", "printf permission-probe": "deny"}
    assert (model.provider, model.model, model.variant) == ("openai", "gpt-5.6-luna", "max")
    assert sanitize_value(payload) == payload
    serialized = json.dumps(payload)
    assert not re.search(r"/(?:home|Users|tmp)/", serialized)
    assert not re.search(r"(?i)(?:bearer\s+|sk-[a-z0-9]|api[_-]?key\s*[=:])", serialized)


def test_live_permission_fixture_is_the_actual_single_explicit_denial_shape() -> None:
    manifest = _manifest()
    path = _CORPUS_DIR / manifest["live_evidence"]["fixtures"]["permission_denial"]["path"]
    payload = json.loads(path.read_text())
    events = parse_run_events(payload["events"])

    assert has_exact_permission_denial(events, command="printf permission-probe") is True


def test_manifest_retains_sanitized_live_diagnostic_summary() -> None:
    manifest = _manifest()
    path = _CORPUS_DIR / manifest["live_evidence"]["fixtures"]["diagnostic"]["path"]
    payload = json.loads(path.read_text())

    assert payload["version"] == PINNED_OPENCODE_VERSION
    assert payload["model"] == {"provider": "openai", "model": "gpt-5.6-luna", "variant": "max"}
    assert payload["classification"] == "degraded"
    assert payload["admissible"] is True
    assert payload["permission_denial"]["explicit_denial"] is True
    assert payload["degraded_absences"] == ["root_hook", "child_sessions"]
    assert sanitize_value(payload) == payload
    serialized = json.dumps(payload)
    assert not re.search(r"/(?:home|Users|tmp)/", serialized)
    assert not re.search(r"(?i)(?:bearer\s+|sk-[a-z0-9]|api[_-]?key\s*[=:])", serialized)


def test_permission_denial_ignores_nonterminal_part_updates_but_rejects_two_terminal_denials() -> None:
    def event(status: str, *, part_id: str = "prt_permission", error: str | None = None) -> OpenCodeRunEvent:
        state: dict[str, object] = {"status": status, "input": {"command": "printf permission-probe"}}
        if status == "pending":
            state["raw"] = "printf permission-probe"
        elif status == "running":
            state["time"] = {"start": 1}
        else:
            state["error"] = (
                error or "The user has specified a rule which prevents you from using this specific tool call."
            )
            state["time"] = {"start": 1, "end": 2}
        return parse_run_event(
            {
                "type": "tool_use",
                "sessionID": "ses_permission",
                "part": {
                    "id": part_id,
                    "sessionID": "ses_permission",
                    "messageID": "msg_permission",
                    "type": "tool",
                    "callID": "call_permission",
                    "tool": "bash",
                    "state": state,
                },
            }
        )

    lifecycle = [event("pending"), event("running"), event("error")]
    assert has_exact_permission_denial(lifecycle, command="printf permission-probe") is True
    canonical = (
        "The user has specified a rule which prevents you from using this specific tool call. "
        'Here are some of the relevant rules [{"permission":"bash","pattern":"printf permission-probe",'
        '"action":"deny"}]'
    )
    assert has_exact_permission_denial([event("error", error=canonical)], command="printf permission-probe") is True
    assert has_exact_permission_denial([event("error", error=canonical)], command="printf other-command") is False
    assert (
        has_exact_permission_denial(
            [*lifecycle, event("error", part_id="prt_permission_duplicate")], command="printf permission-probe"
        )
        is False
    )
    assert (
        has_exact_permission_denial(
            [event("error", error="prefix: The configured permission rule prevented this specific tool call.")],
            command="printf permission-probe",
        )
        is False
    )


def test_manifest_shape_evidence_contains_the_shape_each_row_claims() -> None:
    manifest = _manifest()
    fixtures = dict(_fixtures())
    expected_event_shape = {
        "provider_error": lambda event: event.error is not None,
        "permission_request": lambda event: event.permission is not None,
        "tool_state": lambda event: event.part is not None and event.part.state is not None,
        "compaction_part": lambda event: event.part is not None and event.part.type == "compaction",
        "token_usage": lambda event: event.part is not None and event.part.tokens is not None,
    }
    for shape_name, predicate in expected_event_shape.items():
        for fixture_name in manifest["shape_parsers"][shape_name]["fixtures"]:
            assert any(predicate(event) for event in parse_run_events(fixtures[fixture_name]["events"])), (
                shape_name,
                fixture_name,
            )
    for fixture_name in manifest["shape_parsers"]["child_sessions"]["fixtures"]:
        assert parse_child_sessions(fixtures[fixture_name]["children"])


@pytest.mark.parametrize("name,payload", _fixtures(), ids=lambda item: item if isinstance(item, str) else "fixture")
def test_every_committed_fixture_is_sanitized_and_parsable(name: str, payload: dict[str, Any]) -> None:
    del name
    original = copy.deepcopy(payload)
    events = parse_run_events(payload["events"])
    export = parse_session_export(payload["export"])
    children = parse_child_sessions(payload["children"])
    config = parse_worker_config(payload["config"])
    model = parse_model_reference(payload["model"])

    assert events
    assert export.info.id
    assert isinstance(config.plugins, tuple)
    assert model.provider == "openai"
    assert all(event.session_id == export.info.id for event in events)
    assert all(child.parent_id == export.info.id for child in children)
    assert sanitize_value(payload) == payload
    assert payload == original

    serialized = json.dumps(payload)
    assert not re.search(r"/(?:home|Users|tmp)/", serialized)
    assert not re.search(r"(?i)(?:bearer\s+|sk-[a-z0-9]|api[_-]?key\s*[=:])", serialized)

    if "child_export" in payload:
        child_export = parse_session_export(payload["child_export"])
        assert child_export.info.parent_id == export.info.id


def test_jsonl_parser_uses_the_same_strict_event_parser() -> None:
    payload = _fixtures()[0][1]
    jsonl = "\n".join(json.dumps(event) for event in payload["events"])

    assert parse_run_jsonl(jsonl) == parse_run_events(payload["events"])


def test_unknown_required_event_shape_fails_explicitly() -> None:
    with pytest.raises(UnknownOpenCodeShapeError, match="unknown value"):
        parse_run_event({"type": "future_event", "sessionID": "ses_unknown"})


def test_unknown_required_part_shape_fails_explicitly() -> None:
    with pytest.raises(UnknownOpenCodeShapeError, match=r"part\.type"):
        parse_run_event(
            {
                "type": "text",
                "sessionID": "ses_unknown",
                "part": {
                    "id": "prt_unknown",
                    "sessionID": "ses_unknown",
                    "messageID": "msg_unknown",
                    "type": "future-part",
                },
            }
        )


def test_unknown_required_tool_and_message_shapes_fail_explicitly() -> None:
    payload = _fixtures()[0][1]
    tool_event = copy.deepcopy(payload["events"][3])
    tool_event["part"]["state"]["status"] = "paused"
    with pytest.raises(UnknownOpenCodeShapeError, match="status has unknown value"):
        parse_run_event(tool_event)

    export = copy.deepcopy(payload["export"])
    export["messages"][0]["info"]["role"] = "system"
    with pytest.raises(UnknownOpenCodeShapeError, match="role has unknown value"):
        parse_session_export(export)


def test_event_rejects_a_part_from_another_session() -> None:
    event = copy.deepcopy(_fixtures()[0][1]["events"][0])
    event["part"]["sessionID"] = "ses_other"

    with pytest.raises(OpenCodeShapeError, match=r"part\.sessionID does not match"):
        parse_run_event(event)


def test_malformed_required_usage_and_permission_shapes_fail() -> None:
    with pytest.raises(OpenCodeShapeError, match="cache"):
        parse_run_event(
            {
                "type": "step_finish",
                "sessionID": "ses_bad",
                "part": {
                    "id": "prt_bad",
                    "sessionID": "ses_bad",
                    "messageID": "msg_bad",
                    "type": "step-finish",
                    "reason": "stop",
                    "cost": 0,
                    "tokens": {"input": 1, "output": 2, "reasoning": 0},
                },
            }
        )
    with pytest.raises(UnknownOpenCodeShapeError, match="unknown action"):
        parse_worker_config({"permission": {"bash": "maybe"}, "plugin": ["file:///runner/plugin.ts"]})


@pytest.mark.parametrize("model", ["", "openai/", "/gpt-5.6-luna", "openai/gpt-5.6-luna/extra", "openai/gpt 5.6"])
def test_model_reference_requires_exact_nonempty_provider_and_model_components(model: str) -> None:
    with pytest.raises(OpenCodeShapeError, match="provider/model"):
        parse_model_reference(model)


def test_granular_permission_rules_are_strictly_parsed() -> None:
    config = parse_worker_config(
        {
            "permission": {"*": "ask", "bash": {"*": "allow", "printf permission-probe": "deny"}},
            "plugin": [],
        }
    )

    assert config.permissions["bash"] == {"*": "allow", "printf permission-probe": "deny"}


def test_cursor_admits_a_pending_to_complete_patch_once() -> None:
    pending = CursorRecord.of("msg_tool", "part_tool", {"status": "running", "output": None})
    complete = CursorRecord.of("msg_tool", "part_tool", {"status": "completed", "output": "done"})
    cursor = MessagePartCursor.start()

    first = cursor.admit([pending])
    second = first.cursor.admit([pending, complete])
    third = second.cursor.admit([complete])

    assert [admission.kind for admission in first.admissions] == ["new"]
    assert [admission.kind for admission in second.admissions] == ["updated"]
    assert third.admissions == ()
    assert MessagePartCursor.from_token(second.cursor.token) == second.cursor


def test_cursor_uses_identity_not_array_position_after_compaction() -> None:
    first = MessagePartCursor.start().admit(
        [
            CursorRecord.of("msg_old", "part_old", {"text": "retained"}),
            CursorRecord.of("msg_known", "part_known", {"text": "known"}),
        ]
    )
    after_compaction = first.cursor.admit(
        [
            CursorRecord.of("msg_known", "part_known", {"text": "known"}),
            CursorRecord.of("msg_new", "part_new", {"text": "after compaction"}),
        ]
    )

    assert [(item.identity.message_id, item.identity.part_id) for item in after_compaction.records] == [
        ("msg_new", "part_new")
    ]
    assert "msg_old" in after_compaction.cursor.token
    assert "part_new" in after_compaction.cursor.token


def test_transcript_accepts_open_code_logical_compaction_tail_pruning() -> None:
    payload = copy.deepcopy(_fixtures()[0][1]["export"])
    payload["messages"][1]["parts"][3]["state"] = {
        "status": "running",
        "input": {"command": "printf 'ok\\n' > proof.txt"},
        "time": {"start": 1},
    }
    live = parse_session_export(payload)

    completed_payload = copy.deepcopy(payload)
    completed_payload["messages"][1]["parts"][3]["state"] = {
        "status": "completed",
        "input": {"command": "printf 'ok\\n' > proof.txt"},
        "output": "proof.txt",
        "title": "Write proof file",
        "metadata": {},
        "time": {"start": 1, "end": 2},
    }
    completed_payload["messages"].append(
        {
            "info": {"id": "msg_tail_user", "sessionID": "ses_success", "role": "user"},
            "parts": [
                {
                    "id": "prt_tail_user",
                    "sessionID": "ses_success",
                    "messageID": "msg_tail_user",
                    "type": "text",
                    "text": "Keep this recent turn.",
                }
            ],
        }
    )
    after = parse_session_export(completed_payload)

    compaction_payload = copy.deepcopy(completed_payload)
    compaction_payload["messages"].append(
        {
            "info": {"id": "msg_compaction", "sessionID": "ses_success", "role": "user"},
            "parts": [
                {
                    "id": "prt_compaction",
                    "sessionID": "ses_success",
                    "messageID": "msg_compaction",
                    "type": "compaction",
                    "auto": False,
                }
            ],
        }
    )
    compaction_payload["messages"].append(
        {
            "info": {
                **copy.deepcopy(compaction_payload["messages"][1]["info"]),
                "id": "msg_summary",
                "parentID": "msg_compaction",
            },
            "parts": [
                {
                    "id": "prt_summary",
                    "sessionID": "ses_success",
                    "messageID": "msg_summary",
                    "type": "text",
                    "text": "Summary",
                }
            ],
        }
    )
    first_compaction = parse_session_export(compaction_payload)
    second_compaction_payload = copy.deepcopy(compaction_payload)
    second_compaction_payload["messages"][-2]["parts"][0]["tail_start_id"] = "msg_tail_user"
    second_compaction = parse_session_export(second_compaction_payload)

    proof = inspect_transcript(
        [
            TranscriptExportSample("live", True, live),
            TranscriptExportSample("live_repeat", True, live),
            TranscriptExportSample("after", False, after),
            TranscriptExportSample("compaction", False, first_compaction),
            TranscriptExportSample("compaction_repeat", False, second_compaction),
        ]
    )

    assert proof.valid is True
    assert proof.compaction_observed is True
    assert proof.compaction_pruned is True
    assert proof.appended_after_compaction


def test_transcript_read_and_cursor_failures_are_separately_attributable() -> None:
    export = parse_session_export(_fixtures()[0][1]["export"])

    proof = inspect_transcript(
        [
            TranscriptExportSample("live", True, export),
            TranscriptExportSample("live_repeat", True, export),
            TranscriptExportSample("after", False, export),
            TranscriptExportSample("after_repeat", False, export),
        ]
    )

    # Every export was obtainable, so the read contract holds; no compaction ever happened, so the
    # cursor contract does not.  A caller must be able to tell those two apart.
    assert proof.read_failures == ()
    assert proof.cursor_failures
    assert proof.failures == proof.read_failures + proof.cursor_failures


def test_compaction_phase_exports_cannot_stand_in_for_a_live_turn_read() -> None:
    export = parse_session_export(_fixtures()[0][1]["export"])

    proof = inspect_transcript(
        [
            TranscriptExportSample("after", False, export),
            TranscriptExportSample("after_repeat", False, export),
            TranscriptExportSample("compaction_during_1", True, export, phase="compaction"),
            TranscriptExportSample("compaction_during_2", True, export, phase="compaction"),
        ]
    )

    # The compaction phase reads a separately owned server long after the turn exited, so it can
    # never witness the live-turn read the summary claims.
    assert "no export was captured while the turn was live" in proof.read_failures
    assert "fewer than two exports were captured while the turn was live" in proof.read_failures
    assert proof.during_export is False


def test_transcript_rejects_history_that_returns_after_it_was_removed() -> None:
    payload = _fixtures()[0][1]["export"]
    full = parse_session_export(copy.deepcopy(payload))
    pruned_payload = copy.deepcopy(payload)
    del pruned_payload["messages"][0]
    pruned = parse_session_export(pruned_payload)

    proof = inspect_transcript(
        [
            TranscriptExportSample("live", True, full),
            TranscriptExportSample("live_repeat", True, full),
            TranscriptExportSample("after", False, pruned),
            TranscriptExportSample("after_repeat", False, full),
        ]
    )

    assert proof.retained_history_not_replayed is False
    assert "an identity removed from a later export reappeared in one after it" in proof.cursor_failures


def test_the_child_session_fixture_declares_that_it_is_not_live_evidence() -> None:
    entry = next(item for item in _manifest()["fixtures"] if item["name"] == "child_session")

    # No live run can reach this shape, so the corpus must not read as if one did.
    assert entry["source"] == "synthetic"
    assert "child_sessions" in _manifest()["live_evidence"]["fixtures"]["diagnostic"]["degraded"]


def test_export_cursor_does_not_replay_parts_when_message_usage_changes() -> None:
    payload = _fixtures()[0][1]
    initial_payload = copy.deepcopy(payload["export"])
    initial_payload["messages"][1]["info"].pop("tokens")
    initial_payload["messages"][1]["info"].pop("cost")
    initial_payload["messages"][1]["parts"] = initial_payload["messages"][1]["parts"][:-1]
    initial = parse_session_export(initial_payload)
    completed = parse_session_export(payload["export"])

    first = MessagePartCursor.start().admit(records_for_export(initial))
    second = first.cursor.admit(records_for_export(completed))

    assert [(item.record.identity.part_id, item.kind) for item in second.admissions] == [("prt_success_finish", "new")]


def test_cursor_rejects_malformed_or_versionless_tokens() -> None:
    with pytest.raises(CursorError, match="valid JSON"):
        MessagePartCursor.from_token("not-json")
    with pytest.raises(CursorError, match="unsupported cursor version"):
        MessagePartCursor.from_token('{"version": 2, "seen": []}')


def test_sanitizer_redacts_known_keys_and_embedded_credentials_without_mutating_input() -> None:
    raw: dict[str, object] = {
        "authorization": "Bearer provider-secret",
        "accessToken": "access-secret",
        "nested": {"OPENCODE_API_KEY": "key-secret", "tokens": {"input": 3}},
        "message": "Authorization: Bearer inline-secret OPENCODE_API_KEY=inline-key",
        "url": "https://example.invalid/run?token=query-secret&keep=yes",
    }
    original = copy.deepcopy(raw)

    cleaned = sanitize_value(raw, secrets=("provider-secret", "inline-secret", "inline-key", "query-secret"))

    assert cleaned != raw
    assert cleaned["authorization"] == REDACTED  # type: ignore[index]
    assert cleaned["accessToken"] == REDACTED  # type: ignore[index]
    assert cleaned["nested"] != raw["nested"]  # type: ignore[index]
    assert "provider-secret" not in sanitize_json(raw)
    assert "access-secret" not in sanitize_json(raw)
    assert "key-secret" not in sanitize_json(raw)
    assert "query-secret" not in sanitize_json(raw)
    assert "keep=yes" in sanitize_json(raw)
    assert raw == original


def test_sanitizer_recursively_replaces_paths_in_nested_sequences() -> None:
    raw = {
        "directory": "/home/operator/private/project",
        "nested": ["error at /home/operator/private/project/file.py", ("/tmp/proof/evidence.json",)],
    }

    serialized = sanitize_json(
        raw,
        path_replacements=(("/home/operator/private/project", "/workspace/project"), ("/tmp/proof", "/evidence")),
    )
    assert "/home/operator" not in serialized
    assert "/tmp/proof" not in serialized
    assert "/workspace/project/file.py" in serialized
    assert "/evidence/evidence.json" in serialized


def test_sanitizer_redacts_arbitrary_host_paths_in_nested_structured_values() -> None:
    raw = {
        "/srv/host/private.json": "/opt/opencode/bin/opencode",
        "/var/lib/secret-token": "opaque value",
        "nested": [
            {"workdir": "/home/agent/project", "message": "failed at /var/lib/blizzard/run.json"},
            r"Windows path C:\Users\agent\AppData\OpenCode\state.json",
            r"Windows path with spaces C:\Program Files\OpenCode\state.json",
            "file:///mnt/host-volume/logs/runner.log",
            "cwd:/srv/worker/project/output.txt",
            "network path //build-host/share/output.txt",
        ],
        "canonical": "<workdir>/project",
        "url": "https://example.invalid/path",
    }

    cleaned = sanitize_value(raw)
    serialized = sanitize_json(cleaned)

    for host_path in (
        "/srv/host/private.json",
        "/var/lib/secret-token",
        "/opt/opencode/bin/opencode",
        "/home/agent/project",
        "/var/lib/blizzard/run.json",
        r"C:\Users\agent\AppData\OpenCode\state.json",
        r"C:\Program Files\OpenCode\state.json",
        "file:///mnt/host-volume/logs/runner.log",
        "/srv/worker/project/output.txt",
        "//build-host/share/output.txt",
    ):
        assert host_path not in serialized
    assert "<host-path>/private.json" in serialized
    assert "<host-path>/secret-token" in serialized
    assert "<host-path>/opencode" in serialized
    assert "<host-path>/run.json" in serialized
    assert "<host-path>/state.json" in serialized
    assert "<host-path>/runner.log" in serialized
    assert "<host-path>/output.txt" in serialized
    assert '"canonical":"<workdir>/project"' in serialized
    assert "https://example.invalid/path" in serialized


def test_evidence_redacts_binary_workdir_and_nested_host_paths(tmp_path: Path) -> None:
    report = CompatibilityReport.from_observations(
        PINNED_OPENCODE_VERSION, PINNED_OPENCODE_VERSION, _all_observations()
    )
    binary = "/srv/tools/opencode/bin/opencode"
    workdir = "/home/operator/blizzard"
    scratch = "/tmp/opencode/scratch"
    nested_path = "/var/lib/opencode/session state.json"
    nested_message_path = "/mnt/host/project/error.log"

    runtime = {
        "operations": [
            {"operation": "version", "argv": [binary, "--version"], "cwd": workdir},
            {"operation": "run", "argv": [binary, "run"], "cwd": scratch},
        ],
        "scratch_workdir": scratch,
        "nested": [{"path": nested_path, "message": f"failed while reading {nested_message_path}"}],
    }

    _, runtime_path = OpenCodeEvidence(tmp_path / "evidence").write(report, runtime)
    serialized = runtime_path.read_text()
    sanitized_runtime = json.loads(serialized)

    for host_path in (binary, workdir, scratch, nested_path, nested_message_path):
        assert host_path not in serialized
    assert sanitized_runtime["operations"][0]["argv"][0] == "<binary>"
    assert sanitized_runtime["operations"][0]["cwd"] == "<workdir>"
    assert sanitized_runtime["operations"][1]["cwd"] == "<scratch>"
    assert "<host-path>/session state.json" in serialized
    assert "<host-path>/error.log" in serialized


def test_evidence_replaces_raw_external_ids_with_stable_aliases(tmp_path: Path) -> None:
    report = CompatibilityReport.from_observations(
        PINNED_OPENCODE_VERSION, PINNED_OPENCODE_VERSION, _all_observations()
    )
    runtime = {
        "operations": [
            {
                "argv": ["opencode", "export", "ses_liveRaw123"],
                "stdout": "session=ses_liveRaw123 message=msg_liveRaw456 part=prt_liveRaw789 call=call_liveRaw000",
            }
        ],
        "transcript": {
            "session_id": "ses_liveRaw123",
            "identity": {"message_id": "msg_liveRaw456", "part_id": "prt_liveRaw789"},
        },
    }

    _, runtime_path = OpenCodeEvidence(tmp_path / "evidence").write(report, runtime)
    serialized = runtime_path.read_text()
    sanitized_runtime = json.loads(serialized)

    assert not re.search(r"\b(?:ses|msg|prt|call)_[A-Za-z0-9]+\b", serialized)
    assert sanitized_runtime["operations"][0]["argv"][2] == "<session-1>"
    assert sanitized_runtime["transcript"]["session_id"] == "<session-1>"
    assert sanitized_runtime["transcript"]["identity"] == {
        "message_id": "<message-1>",
        "part_id": "<part-1>",
    }


def test_evidence_aliases_observed_identifiers_without_vendor_prefixes(tmp_path: Path) -> None:
    report = CompatibilityReport.from_observations(
        PINNED_OPENCODE_VERSION, PINNED_OPENCODE_VERSION, _all_observations()
    )
    runtime = {
        "operations": [
            {
                "argv": ["opencode", "export", "opaque-session-42"],
                "http": {"path": "/session/opaque-session-42/children"},
            }
        ],
        "observed_identifiers": {
            "session_id": "opaque-session-42",
            "message_id": "opaque-message-17",
            "part_id": "opaque-part-9",
            "call_id": "opaque-call-3",
        },
        "transcript": {
            "session_id": "opaque-session-42",
            "identity": {"message_id": "opaque-message-17", "part_id": "opaque-part-9"},
        },
    }

    _, runtime_path = OpenCodeEvidence(tmp_path / "evidence").write(report, runtime)
    serialized = runtime_path.read_text()
    sanitized_runtime = json.loads(serialized)

    for identifier in (
        "opaque-session-42",
        "opaque-message-17",
        "opaque-part-9",
        "opaque-call-3",
    ):
        assert identifier not in serialized
    assert sanitized_runtime["operations"][0]["argv"][2] == "<session-1>"
    assert sanitized_runtime["operations"][0]["http"]["path"] == "/session/<session-1>/children"
    assert sanitized_runtime["observed_identifiers"] == {
        "call_id": "<call-1>",
        "message_id": "<message-1>",
        "part_id": "<part-1>",
        "session_id": "<session-1>",
    }


def test_sanitizer_redacts_credential_shapes_without_known_secret_values() -> None:
    raw = (
        'sk-live-regression-value OPENAI_API_KEY="env-only-value" '
        "Authorization: Bearer 'quoted-bearer-value' "
        "https://example.invalid/?access_token=query-only-value&keep=yes "
        "-----BEGIN PRIVATE KEY-----\nserialized-key\n-----END PRIVATE KEY-----"
    )

    cleaned = sanitize_json(raw)

    assert "sk-live-regression-value" not in cleaned
    assert "env-only-value" not in cleaned
    assert "quoted-bearer-value" not in cleaned
    assert "query-only-value" not in cleaned
    assert "serialized-key" not in cleaned
    assert "keep=yes" in cleaned
    assert REDACTED in cleaned


def test_transcript_proof_rejects_a_static_replayed_final_export() -> None:
    final_export = parse_session_export(_fixtures()[0][1]["export"])

    proof = inspect_transcript(
        [
            TranscriptExportSample("during", True, final_export),
            TranscriptExportSample("after", False, final_export),
            TranscriptExportSample("after_repeat", False, final_export),
        ]
    )

    assert proof.valid is False
    assert proof.pending_to_completed is False
    assert proof.compaction_observed is False
    assert proof.appended_after_compaction == ()


def test_attach_signal_accepts_opencode_global_event_stream() -> None:
    signal = OpenCodeAttachSignal(
        (
            OpenCodeAttachRequest("GET", "/session/ses_probe", 200, session_matches=True, directory_matches=True),
            OpenCodeAttachRequest(
                "GET",
                "/global/event",
                200,
                content_type="text/event-stream",
                event_stream_valid=True,
                event_stream_bytes=12,
            ),
        ),
        session_matches=True,
        directory_matches=True,
        client_alive_after_handshake=True,
        continuation_observed=True,
    )

    assert signal.event_status == 200
    assert signal.observed is True


def test_attach_proxy_preserves_buffered_response_content_encoding() -> None:
    body = gzip.compress(b'{"ok":true}')

    class Response:
        def __init__(self) -> None:
            self.status = 200
            self.headers = {
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
                "Transfer-Encoding": "chunked",
                "X-Upstream-Header": "preserved",
            }

        def read(self, amount: int = -1) -> bytes:
            del amount
            return body

        def close(self) -> None:
            return None

    class Handler:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.status: int | None = None
            self.wfile = io.BytesIO()

        def send_response(self, status: int) -> None:
            self.status = status

        def send_header(self, name: str, value: str) -> None:
            self.headers[name.lower()] = value

        def end_headers(self) -> None:
            return None

    proxy = OpenCodeAttachProxy(
        "http://127.0.0.1:4096",
        session_id="ses_probe",
        directory=Path.cwd(),
        transport=UrllibLoopbackTransport(),
    )
    request_index = proxy._record_request(OpenCodeAttachRequest("GET", "/config", None))
    handler = Handler()

    proxy._forward_buffered(handler, "/config", LoopbackResponse(Response()), request_index)  # type: ignore[arg-type]

    assert handler.status == 200
    assert handler.headers["content-type"] == "application/json"
    assert handler.headers["content-encoding"] == "gzip"
    assert handler.headers["x-upstream-header"] == "preserved"
    assert "transfer-encoding" not in handler.headers
    assert handler.headers["content-length"] == str(len(body))
    assert handler.wfile.getvalue() == body


def test_loopback_response_serializes_close_after_interrupting_a_read() -> None:
    read_started = threading.Event()
    release_read = threading.Event()
    closed = threading.Event()
    active_read = False

    class Response:
        def __init__(self) -> None:
            self.status = 200
            self.headers: dict[str, str] = {}

        def read1(self, amount: int) -> bytes:
            del amount
            nonlocal active_read
            active_read = True
            read_started.set()
            release_read.wait(2.0)
            active_read = False
            return b""

        def abort(self) -> None:
            release_read.set()

        def close(self) -> None:
            assert active_read is False
            closed.set()

    response = LoopbackResponse(Response())
    errors: list[BaseException] = []

    def read() -> None:
        try:
            assert response.read_chunk() == b""
        except BaseException as error:
            errors.append(error)

    reader = threading.Thread(target=read)
    reader.start()
    assert read_started.wait(1.0)

    response.close()
    reader.join(timeout=1.0)

    assert not reader.is_alive()
    assert errors == []
    assert closed.is_set()
    assert response.read_chunk() == b""


def test_loopback_transport_is_direct_loopback_only_and_preserves_local_request_shape() -> None:
    requests: list[tuple[str, str, dict[str, str], bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_PATCH(self) -> None:
            if self.path == "/redirect":
                self.send_response(307)
                self.send_header("Location", "/echo")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/external-redirect":
                self.send_response(307)
                self.send_header("Location", "http://example.com:80/escape")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0"))
            requests.append((self.command, self.path, dict(self.headers), self.rfile.read(length)))
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        transport = UrllibLoopbackTransport()
        base_url = f"http://127.0.0.1:{server.server_port}"
        headers = {
            "Authorization": "Bearer should-not-cross",
            "Cookie": "session=should-not-cross",
            "X-OpenCode-Directory": "/tmp/compatibility",
            "Accept": "application/json",
        }
        with transport.request(
            LoopbackRequest("PATCH", f"{base_url}/redirect", headers=headers, body=b"request-body"),
            timeout=2.0,
        ) as response:
            assert response.status == 200
            assert response.read() == b"ok"

        method, path, received_headers, body = requests[-1]
        lower_headers = {name.lower(): value for name, value in received_headers.items()}
        assert (method, path, body) == ("PATCH", "/echo", b"request-body")
        assert lower_headers["x-opencode-directory"] == "/tmp/compatibility"
        assert lower_headers["accept"] == "application/json"
        assert "authorization" not in lower_headers
        assert "cookie" not in lower_headers

        with (
            pytest.raises(LoopbackTransportError, match="loopback"),
            transport.request(LoopbackRequest("PATCH", f"{base_url}/external-redirect"), timeout=2.0),
        ):
            pass
        with (
            pytest.raises(LoopbackTransportError, match="loopback"),
            transport.request(LoopbackRequest("GET", "http://example.com:80/escape"), timeout=2.0),
        ):
            pass
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
