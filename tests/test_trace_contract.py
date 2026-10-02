"""The trace contract (unit tier, blizzard:trace-contract) — ``contracts/traces/``."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from blizzard.foundation import trace_attributes as shared
from blizzard.foundation.node_steps import Executor
from blizzard.foundation.platform_tracing import attributes as platform_attr
from blizzard.foundation.platform_tracing.semconv import DATABASE_SEMCONV_VERSION, HTTP_SEMCONV_VERSION
from blizzard.foundation.trace_ids import DerivedContext, RunnerSpanRole, SpanRole, StepKey, span_id, trace_id
from blizzard.foundation.trace_spans import EventRecord, LinkRecord, SpanRecord
from blizzard.hub.domain.tracing import attributes as attr
from blizzard.hub.domain.tracing.assembly import assemble_step
from blizzard.hub.domain.tracing.facts import (
    BounceRecord,
    DecisionRecord,
    DecisionResolutionRecord,
    EpochOwnerRecord,
    EscalationRecord,
    HubExecSlotRecord,
    HubPollRecord,
    LeaseRecord,
    MigrationRecord,
    PauseRecord,
    PromotionRecord,
    QuestionRecord,
    RequeueRecord,
    RestartRecord,
    RouteCreatedRecord,
    RouteReleasedRecord,
    StepFacts,
)
from blizzard.hub.domain.tracing.steps import identify_steps
from blizzard.hub.domain.work import UsageFact
from blizzard.runner.domain.tracing import attributes as runner_attr
from blizzard.runner.domain.tracing import platform as runner_platform
from blizzard.runner.domain.tracing.assembly import assemble_lease
from blizzard.runner.domain.tracing.facts import LeaseTraceFacts
from tests import runner_trace_fixtures as rfx
from tests import trace_fixtures as fx
from tests.repo_files import repo_root
from tests.trace_contract_support import PARAMETERIZED_NAMES, dictionary, otlp_type, required_by_role, role_of_name

pytestmark = pytest.mark.unit

_ROOT = repo_root()
_CONTRACT_DIR = _ROOT / "contracts" / "traces"
_GOLDEN_DIR = _CONTRACT_DIR / "golden"
_TRACING_DOC = _ROOT / "docs" / "deployment" / "tracing.md"
_VERSIONING_DOC = _ROOT / "docs" / "versioning.md"
_REGEN_VARIABLE = "BLIZZARD_REGEN_TRACE_CONTRACT"
_REGEN_COMMAND = f"{_REGEN_VARIABLE}=1 uv run pytest tests/test_trace_contract.py"
_ATTRIBUTE_TYPES = {"string", "int", "double", "bool", "string[]"}
_ROLES: dict[str, SpanRole | RunnerSpanRole] = {role.value: role for role in (*SpanRole, *RunnerSpanRole)}


def _graph() -> Any:
    names = ("build", "review", "verify", "gate")
    nodes = [fx.node("g1", n, Executor.HUB if n == "verify" else Executor.RUNNER) for n in names]
    return replace(fx.graph("g1", *names), nodes=nodes)


def _usage(epoch: int, at: int, **kw: Any) -> UsageFact:
    fields: dict[str, Any] = {
        "node_id": "g1-build",
        "epoch": epoch,
        "kind": "spawn",
        "model": "claude-x",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 1000,
        "cache_create_tokens": 10,
        "cost_usd": 0.5,
        "recorded_at": fx.at(at),
        "harness_id": "claude-code",
        "harness_version": "2.0",
        "estimated_cost_usd": 0.25,
    }
    fields.update(kw)
    return UsageFact(**fields)


def _journey() -> StepFacts:
    """Every attribute, event and span name: usage, asks, a pause, a bounce, a gate, a hub step and an escalation."""
    return StepFacts(
        chunk_id="ch_1",
        graphs={"g1": _graph(), "g2": fx.G2},
        pin_graph_id="g1",
        work_refs=("acme#42",),
        promotions=(PromotionRecord(fx.at(2)),),
        routes_created=(RouteCreatedRecord(fx.at(3)), RouteCreatedRecord(fx.at(32))),
        pauses=(PauseRecord("p1", True, fx.at(12)), PauseRecord("p2", False, fx.at(15))),
        questions=(
            QuestionRecord("q1", 1, fx.at(16), fx.at(18)),
            QuestionRecord("q2", 1, fx.at(20), fx.at(19)),
        ),
        usage=(_usage(1, 14), _usage(1, 22, cost_usd=None, estimated_cost_usd=None, harness_version=None)),
        requeues=(RequeueRecord(fx.at(56)),),
        bounces=(BounceRecord(2, "conflict", fx.at(50)), BounceRecord(4, "checks", fx.at(126))),
        transitions=(
            fx.to("g1", "review", 30, 1, choice_name="pass"),
            fx.to("g1", "build", 55, 2, choice_name="fail"),
            fx.to("g1", "gate", 70, 3),
            fx.to("g1", "verify", 120, 4, decision_id="d1", choice_name="approve"),
            fx.to("g1", "build", 130, 4),
        ),
        decisions=(DecisionRecord("d1", "g1-gate", 3, fx.at(71)),),
        decision_resolutions=(DecisionResolutionRecord("d1", fx.at(100), choice="approve"),),
        hub_polls=(HubPollRecord("hp1", "g1-verify", 4, fx.at(122)), HubPollRecord("hp2", "g1-verify", 4, fx.at(124))),
        hub_exec_slots=(HubExecSlotRecord("s1", "g1-verify", fx.at(123), fx.at(125)),),
        escalations=(EscalationRecord(5, fx.at(150)),),
        epoch_owners=(
            EpochOwnerRecord(1, "r-1", fx.at(9)),
            EpochOwnerRecord(2, "r-1", fx.at(34)),
            EpochOwnerRecord(3, "r-1", fx.at(59)),
            EpochOwnerRecord(4, None, fx.at(120)),
            EpochOwnerRecord(5, "r-1", fx.at(139)),
        ),
        lease_facts=(
            LeaseRecord(1, fx.at(10)),
            LeaseRecord(2, fx.at(35)),
            LeaseRecord(3, fx.at(60)),
            LeaseRecord(4, fx.at(125)),
            LeaseRecord(5, fx.at(140)),
        ),
    )


def _link_restart() -> StepFacts:
    return fx.make_facts(
        transitions=(fx.to("g1", "review", 30, 1), fx.to("g1", "gate", 90, 6)),
        restarts=(RestartRecord(5, fx.at(35), "g1", "g1-review"),),
        epoch_owners=(
            EpochOwnerRecord(1, "r-1", fx.at(9)),
            EpochOwnerRecord(5, None, fx.at(35)),
            EpochOwnerRecord(6, "r-1", fx.at(49)),
        ),
        lease_facts=(LeaseRecord(1, fx.at(10)), LeaseRecord(6, fx.at(50))),
    )


def _link_migration() -> StepFacts:
    return fx.make_facts(
        migrations=(MigrationRecord(1, fx.at(30), "g1", "g2", from_node_id="g1-build"),),
        transitions=(fx.to("g1", "gate", 90, 2),),
        **fx.merge(fx.runner_epoch(1, 10), fx.runner_epoch(2, 50)),
    )


def _link_retry() -> StepFacts:
    return fx.make_facts(
        route_released=(RouteReleasedRecord(fx.at(30)),),
        transitions=(fx.to("g1", "gate", 90, 2),),
        **fx.merge(fx.runner_epoch(1, 10), fx.runner_epoch(2, 50)),
    )


SCENARIOS = {
    "journey": _journey,
    "link-restart": _link_restart,
    "link-migration": _link_migration,
    "link-retry": _link_retry,
}


def _runner_judged() -> LeaseTraceFacts:
    facts = rfx.make_facts(
        boundaries=(rfx.boundary(1, 1, "spawn", 1, 100), rfx.boundary(2, 1, "judge", 50, 100)),
        usage=(
            rfx.usage(1, 1, "spawn", 30),
            rfx.usage(2, 1, "judge", 60, model="haiku", cost_usd=None, harness_id="opencode", harness_version="0.9"),
        ),
    )
    return rfx.with_context(facts, session_name=None)


RUNNER_SCENARIOS = {
    "runner-lease": rfx.busy_facts,
    "runner-judged": _runner_judged,
}


def _declared_events(module: object) -> set[str]:
    return {value for name, value in vars(module).items() if name.startswith("EVENT_")}


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _value(value: object) -> object:
    return list(value) if isinstance(value, tuple) else value


def _attributes(attributes: Any) -> dict[str, object]:
    return {key: _value(attributes[key]) for key in sorted(attributes)}


def _event(event: EventRecord) -> dict[str, object]:
    return {"name": event.name, "time": _instant(event.time), "attributes": _attributes(event.attributes)}


def _link(link: LinkRecord) -> dict[str, object]:
    ctx = link.context
    return {
        "trace_id": f"{ctx.trace_id:032x}",
        "span_id": f"{ctx.span_id:016x}",
        "attributes": _attributes(link.attributes),
    }


def _span(span: SpanRecord) -> dict[str, object]:
    return {
        "trace_id": f"{span.context.trace_id:032x}",
        "span_id": f"{span.context.span_id:016x}",
        "parent_span_id": None if span.parent_span_id is None else f"{span.parent_span_id:016x}",
        "name": span.name,
        "kind": span.kind.value,
        "status": span.status.value,
        "start": _instant(span.start),
        "end": _instant(span.end),
        "attributes": _attributes(span.attributes),
        "events": [_event(e) for e in span.events],
        "links": [_link(link) for link in span.links],
    }


def _serialize(facts: StepFacts) -> str:
    steps = [s for s in identify_steps(facts) if s.close is not None]
    spans = [_span(span) for step in steps for span in assemble_step(facts, step)]
    return json.dumps(spans, indent=2, sort_keys=True) + "\n"


def _serialize_lease(facts: LeaseTraceFacts) -> str:
    return json.dumps([_span(span) for span in assemble_lease(facts)], indent=2, sort_keys=True) + "\n"


def _live() -> dict[str, str]:
    hub = {name: _serialize(build()) for name, build in SCENARIOS.items()}
    return hub | {name: _serialize_lease(build()) for name, build in RUNNER_SCENARIOS.items()}


def _golden() -> dict[str, list[dict[str, Any]]]:
    return {path.stem: json.loads(path.read_text()) for path in sorted(_GOLDEN_DIR.glob("*.json"))}


@pytest.fixture(scope="module", autouse=True)
def _regenerate_if_asked() -> None:
    if os.environ.get(_REGEN_VARIABLE) != "1":
        return
    _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for stale in _GOLDEN_DIR.glob("*.json"):
        stale.unlink()
    for name, text in _live().items():
        (_GOLDEN_DIR / f"{name}.json").write_text(text)


def _observed() -> tuple[set[str], set[str], set[str], set[str], set[tuple[str, str]]]:
    names: set[str] = set()
    events: set[str] = set()
    reasons: set[str] = set()
    keys: set[str] = set()
    carried: set[tuple[str, str]] = set()
    for spans in _golden().values():
        for span in spans:
            role = role_of_name(span["name"])
            names.add(PARAMETERIZED_NAMES.get(role) or span["name"])
            for key in span["attributes"]:
                keys.add(key)
                carried.add((role, key))
            for event in span["events"]:
                events.add(event["name"])
                for key in event["attributes"]:
                    keys.add(key)
                    carried.add((f"event:{event['name']}", key))
            for link in span["links"]:
                reasons.add(str(link["attributes"][attr.LINK_REASON]))
                for key in link["attributes"]:
                    keys.add(key)
                    carried.add(("link", key))
    return names, events, reasons, keys, carried


def test_the_live_assembly_equals_the_golden() -> None:
    live = _live()
    golden = {path.stem: path.read_text() for path in sorted(_GOLDEN_DIR.glob("*.json"))}
    assert set(live) == set(golden), f"golden scenarios differ from the seeds; regenerate with `{_REGEN_COMMAND}`"
    drifted = sorted(name for name in live if live[name] != golden[name])
    assert not drifted, (
        f"the assembled spans for {drifted} have drifted from contracts/traces/golden/; if the shape change is "
        f"intended, edit contracts/traces/dictionary.json and regenerate with `{_REGEN_COMMAND}`"
    )


def test_the_dictionary_names_exactly_the_attributes_the_code_declares() -> None:
    d = dictionary()
    named = {a["name"] for a in d["attributes"]} | {a["name"] for a in d["resource_attributes"]}
    assert named == attr.DECLARED_ATTRIBUTES | runner_attr.DECLARED_ATTRIBUTES
    resource = {a["name"] for a in d["resource_attributes"]}
    assert resource == set(attr.resource_attributes({}, "0")) == set(runner_attr.resource_attributes({}, "0"))


def test_the_dictionary_constants_equal_the_code_constants() -> None:
    d = dictionary()
    assert d["instrumentation_scopes"] == [
        {"name": attr.INSTRUMENTATION_SCOPE, "version": attr.INSTRUMENTATION_SCOPE_VERSION},
        {"name": runner_attr.INSTRUMENTATION_SCOPE, "version": runner_attr.INSTRUMENTATION_SCOPE_VERSION},
    ]
    assert d["schema_version"] == shared.SCHEMA_VERSION
    assert d["genai_semconv_version"] == shared.GENAI_SEMCONV_VERSION
    assert d["event_names"] == sorted(_declared_events(attr) | _declared_events(runner_attr))


def test_the_dictionary_roles_are_the_code_roles() -> None:
    d = dictionary()
    assert {entry["role"] for entry in d["spans"]} == set(_ROLES)
    for entry in d["spans"]:
        hub = isinstance(_ROLES[entry["role"]], SpanRole)
        assert entry["scope"] == (attr.INSTRUMENTATION_SCOPE if hub else runner_attr.INSTRUMENTATION_SCOPE), entry[
            "role"
        ]
    for entry in d["attributes"]:
        assert entry["type"] in _ATTRIBUTE_TYPES, entry["name"]
        assert entry["meaning"], entry["name"]
        assert set(entry.get("optional_on", ())) <= set(entry["on"]), entry["name"]


def test_the_golden_shape_is_the_dictionary_shape() -> None:
    d = dictionary()
    names, events, reasons, keys, carried = _observed()
    assert names == {entry["name"] for entry in d["spans"]}
    assert events == set(d["event_names"])
    assert reasons == {entry["name"] for entry in d["link_reasons"]}
    assert keys == {a["name"] for a in d["attributes"]}
    declared = {(role, a["name"]) for a in d["attributes"] for role in a["on"]}
    assert carried <= declared


def test_a_required_attribute_rides_every_span_that_carries_it() -> None:
    required = required_by_role()
    for spans in _golden().values():
        for span in spans:
            role = role_of_name(span["name"])
            for key, on in required.items():
                if role in on:
                    assert key in span["attributes"], f"{span['name']} lacks required {key}"
            for event in span["events"]:
                for key, on in required.items():
                    if f"event:{event['name']}" in on:
                        assert key in event["attributes"], f"{event['name']} lacks required {key}"


def test_every_golden_value_has_its_dictionary_type() -> None:
    types = {a["name"]: a["type"] for a in dictionary()["attributes"]}

    for spans in _golden().values():
        for span in spans:
            bags = [span["attributes"], *(e["attributes"] for e in span["events"])]
            bags += [link["attributes"] for link in span["links"]]
            for bag in bags:
                for key, value in bag.items():
                    assert otlp_type(value) == types[key], key


def test_the_id_vectors_reproduce_through_the_derivation() -> None:
    ids = dictionary()["ids"]
    assert ids["hash"] == "sha256"
    assert ids["trace"]["bytes"] == 16
    assert ids["span"]["bytes"] == 8
    for vector in ids["vectors"]:
        key = StepKey(vector["chunk_id"], vector["epoch"], vector.get("decision_id"))
        assert key.text() == vector["key"]
        assert f"{trace_id(key):032x}" == vector["trace_id"]
        role = _ROLES[vector["role"]]
        assert f"{span_id(key, role, vector['discriminator']):016x}" == vector["span_id"]
        assert DerivedContext.of(key, role, vector["discriminator"]).trace_flags == 1
        if "parent_span_id" in vector:
            assert vector["parent_span_id"] == f"{span_id(key, SpanRole.STEP):016x}"


def test_a_runner_worker_vector_parents_into_the_step_vector_of_its_attempt() -> None:
    vectors = dictionary()["ids"]["vectors"]
    workers = [v for v in vectors if v["role"] == RunnerSpanRole.WORKER]
    assert workers
    for worker in workers:
        step = next(v for v in vectors if v["role"] == SpanRole.STEP and v["key"] == worker["key"])
        assert (worker["trace_id"], worker["parent_span_id"]) == (step["trace_id"], step["span_id"])


def test_the_trace_id_prefix_and_span_prefix_match_thedictionary() -> None:
    ids = dictionary()["ids"]
    key = StepKey.attempt("ch_x", 1)
    assert f"{trace_id(key):032x}" == hashlib.sha256((ids["trace"]["prefix"] + "ch_x/1").encode()).hexdigest()[:32]
    expected = hashlib.sha256((ids["span"]["prefix"] + "ch_x/1/step/").encode()).hexdigest()[:16]
    assert f"{span_id(key, SpanRole.STEP):016x}" == expected


def _table_rows(text: str, heading: str) -> list[list[str]]:
    section = text.split(f"\n{heading}\n", 1)[1]
    rows: list[list[str]] = []
    for line in section.splitlines():
        if line.startswith("|"):
            rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
        elif rows:
            break
    return rows[2:]


def _unticked(cell: str) -> str:
    return cell.strip("`")


def test_the_published_attribute_table_is_thedictionary() -> None:
    rows = _table_rows(_TRACING_DOC.read_text(), "## Attributes")
    published = [(_unticked(r[0]), _unticked(r[1]), r[2]) for r in rows]
    d = dictionary()
    authored = [(a["name"], a["type"], a["meaning"]) for a in d["attributes"]]
    assert published == authored


def test_the_platform_section_is_the_code_constants() -> None:
    platform = dictionary()["platform"]
    assert platform["instrumentation_scopes"] == [
        {"name": attr.PLATFORM_INSTRUMENTATION_SCOPE, "version": attr.PLATFORM_INSTRUMENTATION_SCOPE_VERSION},
        {
            "name": runner_platform.PLATFORM_INSTRUMENTATION_SCOPE,
            "version": runner_platform.PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
        },
        {"name": platform_attr.CLI_SCOPE, "version": "1"},
    ]
    assert platform["http_semconv_version"] == HTTP_SEMCONV_VERSION
    assert platform["database_semconv_version"] == DATABASE_SEMCONV_VERSION
    assert {a["name"] for a in platform["attributes"]} == {
        platform_attr.CALLER,
        shared.CHUNK_ID,
        platform_attr.LEASE_ID,
        attr.RUNNER_ID,
        runner_platform.TICK_STEP,
        *platform_attr.CLI_ATTRIBUTES,
    }
    assert attr.RUNNER_ID == runner_attr.RUNNER_ID
    declared = {a["name"]: a["type"] for a in platform["attributes"]}
    assert all(declared[name] == type_ for name, type_ in platform_attr.CLI_ATTRIBUTES.items())
    assert all(t == "string" for n, t in declared.items() if n not in platform_attr.CLI_ATTRIBUTES)


def test_the_published_platform_attribute_table_is_thedictionary() -> None:
    rows = _table_rows(_TRACING_DOC.read_text(), "### Platform attributes")
    published = [(_unticked(r[0]), _unticked(r[1]), r[2]) for r in rows]
    authored = [(a["name"], a["type"], a["meaning"]) for a in dictionary()["platform"]["attributes"]]
    assert published == authored


def test_the_platform_section_of_the_page_states_the_dictionary_versions_and_scopes() -> None:
    section = _TRACING_DOC.read_text().split("\n## Platform spans\n", 1)[1]
    platform = dictionary()["platform"]
    for scope in platform["instrumentation_scopes"]:
        assert f"`{scope['name']}`" in section
    assert platform["http_semconv_version"] in section
    assert platform["database_semconv_version"] in section


def test_the_published_resource_table_is_thedictionary() -> None:
    rows = _table_rows(_TRACING_DOC.read_text(), "## Resource attributes")
    published = [(_unticked(r[0]), _unticked(r[1]), r[2]) for r in rows]
    authored = [(a["name"], a["type"], a["meaning"]) for a in dictionary()["resource_attributes"]]
    assert published == authored


def test_the_published_span_table_is_thedictionary() -> None:
    rows = _table_rows(_TRACING_DOC.read_text(), "## Spans")
    published = [(_unticked(r[0]), _unticked(r[1]), r[2]) for r in rows]
    authored = [(s["name"], s["role"], s["meaning"]) for s in dictionary()["spans"]]
    assert published == authored


def test_versioning_names_the_trace_schema_version() -> None:
    text = _VERSIONING_DOC.read_text()
    assert shared.TRACE_SCHEMA_VERSION in text
    assert re.search(r"trace contract", text, re.IGNORECASE)
