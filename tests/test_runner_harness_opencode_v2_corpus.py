"""The committed OpenCode 2 corpus, read back through the client and the proof's own part readers: its manifest meets the
corpus contract, every fixture it lists is present, and the captured shapes still parse the way the proof read them."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from blizzard.runner.harness.admission import classification_of_manifest
from blizzard.runner.harness.compatibility import CompatibilityClassification
from blizzard.runner.harness.internal.committed_corpus import CommittedCorpus
from blizzard.runner.harness.json_value import JsonObject, JsonValue
from blizzard.runner.harness.opencode_v2.client import OpenCodeServiceClient
from blizzard.runner.harness.opencode_v2.compatibility.proof import (
    as_object,
    event_session,
    final_assistant_text,
    part_error_type,
    part_state,
    tool_parts,
)
from blizzard.runner.harness.opencode_v2.compatibility.rows import DEGRADABLE_ROWS, ROW_ROSTER
from blizzard.runner.harness.opencode_v2.transport import ServiceRequest
from blizzard.runner.harness.opencode_v2.version import ADMITTED_OPENCODE_V2_RANGE
from tests.opencode_v2_fakes import REGISTRATION, FakeService, FakeTransport

pytestmark = pytest.mark.unit

_CORPUS = CommittedCorpus()
_VERSIONS = sorted(version for version in _CORPUS.versions("opencode") if version in ADMITTED_OPENCODE_V2_RANGE)


def _directory(version: str) -> Path:
    return _CORPUS.root / "opencode" / version


def _fixture(version: str, name: str) -> JsonValue:
    return cast("JsonValue", json.loads((_directory(version) / f"{name}.json").read_text()))


def _replaying(path: str, body: JsonValue) -> OpenCodeServiceClient:
    """A client whose service answers ``GET path`` (query aside) with ``body`` under ``data``."""

    def answer(_: ServiceRequest) -> tuple[int, JsonValue]:
        return 200, {"data": body}

    service = FakeService(routes={("GET", path): answer})
    return OpenCodeServiceClient(REGISTRATION, FakeTransport(service))


def _objects(value: JsonValue) -> list[JsonObject]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def test_an_opencode_2_corpus_is_committed() -> None:
    assert _VERSIONS


@pytest.mark.parametrize("version", _VERSIONS)
def test_the_manifest_meets_the_corpus_contract_and_lists_only_present_fixtures(version: str) -> None:
    manifest = _CORPUS.manifest("opencode", version)
    assert manifest is not None
    assert manifest["major_version"] == 2 and manifest["version"] == version
    classification = classification_of_manifest(manifest)
    assert classification in {CompatibilityClassification.SUPPORTED, CompatibilityClassification.DEGRADED}
    rows = _objects(manifest["rows"])
    assert [row["name"] for row in rows] == [row.value for row in ROW_ROSTER]
    degraded = {str(row["name"]) for row in rows if row["classification"] != "supported"}
    assert degraded <= {row.value for row in DEGRADABLE_ROWS}
    for fixture in _objects(manifest["fixtures"]):
        assert (_directory(version) / str(fixture["path"])).is_file(), fixture


@pytest.mark.parametrize("version", _VERSIONS)
def test_the_captured_judgement_reads_back_through_the_client_with_its_choice(version: str) -> None:
    client = _replaying("/api/session/ses_j/message", _fixture(version, "judgement_messages"))
    assert "<Choice>" in final_assistant_text(client.messages("ses_j"))


@pytest.mark.parametrize("version", _VERSIONS)
def test_the_captured_denials_carry_the_permission_rejected_error(version: str) -> None:
    denied = _objects(_fixture(version, "permission_denied_parts"))
    assert denied and all(part_error_type(part) == "permission.rejected" for part in denied)


@pytest.mark.parametrize("version", _VERSIONS)
def test_the_captured_child_spawn_records_the_child_session_id(version: str) -> None:
    parts = tool_parts([{"type": "assistant", "content": _fixture(version, "child_spawn_parts")}], "subagent")
    recorded = [as_object(part_state(part).get("metadata")).get("sessionID") for part in parts]
    assert recorded and all(isinstance(session_id, str) and session_id.startswith("ses_") for session_id in recorded)


@pytest.mark.parametrize("version", _VERSIONS)
def test_every_captured_session_event_names_its_session(version: str) -> None:
    events = [
        event for event in _objects(_fixture(version, "events")) if str(event.get("type", "")).startswith("session.")
    ]
    assert events and all(event_session(event) is not None for event in events)


@pytest.mark.parametrize("version", _VERSIONS)
def test_the_captured_model_listing_reads_back_through_the_client(version: str) -> None:
    models = _replaying("/api/model", _fixture(version, "model_listing")).models()
    assert models and all("id" in model for model in models)
