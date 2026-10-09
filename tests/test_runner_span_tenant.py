"""The runner's spans name its tenant beside its id and name: its lease spans, the worker telemetry it
stamps, and its own platform spans — each once a hub that names the tenant has answered, and none before."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.foundation.cli_spans import SCOPE_NAME
from blizzard.foundation.platform_tracing.attributes import CLI_ATTRIBUTES
from blizzard.foundation.platform_tracing.received import ReceivedLogRecord, ReceivedSpan
from blizzard.foundation.trace_ids import chunk_trace_id
from blizzard.runner.hub.identity import RunnerIdentity, RunnerIdentityHolder
from blizzard.runner.leases.model import Lease
from blizzard.runner.tracing.assembly import assemble_lease
from blizzard.runner.tracing.attributes import DECLARED_ATTRIBUTES, RUNNER_ID, RUNNER_NAME, TENANT, TENANT_NAME
from blizzard.runner.tracing.facts import RunnerFact
from blizzard.runner.tracing.platform import identity_stamp
from blizzard.runner.tracing.receiver import Allowlist, admit, admit_log_records
from tests import runner_trace_fixtures as rfx
from tests.runner_fakes import REGISTERED_AT

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)
_BRAVO = RunnerIdentity(
    runner_id="rn_01J9ZRUNNER",
    runner_name="r-claude",
    registered_at=REGISTERED_AT,
    tenant_id="tn_bravo",
    tenant_name="bravo",
)
_PRE_TENANT = replace(_BRAVO, tenant_id=None, tenant_name=None)
_FORGED = {TENANT: "tn_forged", TENANT_NAME: "forged"}


def _lease() -> Lease:
    return Lease(
        lease_id="lease_1",
        chunk_id="ch_1",
        graph_id="gr_1",
        node_id="nd_build",
        node_name="build",
        epoch=1,
        retries_max=2,
        created_at=_NOW,
    )


def _span(attributes: dict[str, object]) -> ReceivedSpan:
    return ReceivedSpan(
        trace_id=chunk_trace_id("ch_1"),
        span_id=0x00F067AA0BA902B7,
        parent_span_id=None,
        name="blizzard artifact create",
        kind=1,
        start_time_ns=1_000,
        end_time_ns=2_000,
        status_code=1,
        scope_name=SCOPE_NAME,
        scope_version="1",
        attributes=attributes,  # type: ignore[arg-type]
    )


def _log(attributes: dict[str, object]) -> ReceivedLogRecord:
    return ReceivedLogRecord(
        time_ns=1_000,
        observed_time_ns=1_000,
        severity_number=9,
        severity_text="INFO",
        body="tool used",
        trace_id=None,
        span_id=None,
        trace_flags=0,
        scope_name="com.anthropic.claude_code.events",
        scope_version="1",
        attributes=attributes,  # type: ignore[arg-type]
    )


def test_the_tenant_names_are_declared_runner_span_attributes() -> None:
    assert {TENANT, TENANT_NAME} <= DECLARED_ATTRIBUTES
    assert (TENANT, TENANT_NAME) == ("blizzard.tenant", "blizzard.tenant_name")


def test_every_lease_span_carries_the_runners_tenant_beside_its_id_and_name() -> None:
    facts = rfx.busy_facts()
    facts = replace(facts, runner=RunnerFact(facts.runner.runner_id, facts.runner.runner_name, "tn_bravo", "bravo"))

    spans = assemble_lease(facts)

    assert spans
    for span in spans:
        assert span.attributes[RUNNER_ID] == facts.runner.runner_id
        assert (span.attributes[TENANT], span.attributes[TENANT_NAME]) == ("tn_bravo", "bravo")


def test_a_lease_span_names_no_tenant_before_a_hub_names_one() -> None:
    spans = assemble_lease(rfx.busy_facts())

    assert spans
    assert all(TENANT not in s.attributes and TENANT_NAME not in s.attributes for s in spans)


def test_a_workers_span_is_stamped_with_the_runners_tenant_over_any_it_sent() -> None:
    allowlist = Allowlist(
        scope=SCOPE_NAME, attributes={**CLI_ATTRIBUTES, **dict.fromkeys(_FORGED, "string")}, stamp_runner=True
    )
    sent = _span({"blizzard.cli.command": "artifact create", **_FORGED})

    (stamped,) = admit([sent], _lease(), allowlist, runner=_BRAVO).kept

    assert (stamped.attributes[TENANT], stamped.attributes[TENANT_NAME]) == ("tn_bravo", "bravo")
    assert stamped.attributes[RUNNER_NAME] == "r-claude"


def test_a_runner_that_knows_no_tenant_strips_the_one_a_worker_sent() -> None:
    (record,) = admit_log_records(
        [_log(dict(_FORGED))], _lease(), frozenset({"com.anthropic.claude_code.events"}), runner=_PRE_TENANT
    ).kept

    assert TENANT not in record.attributes and TENANT_NAME not in record.attributes
    assert record.attributes[RUNNER_ID] == _BRAVO.runner_id


def test_a_workers_log_record_carries_the_runners_tenant() -> None:
    (record,) = admit_log_records(
        [_log({})], _lease(), frozenset({"com.anthropic.claude_code.events"}), runner=_BRAVO
    ).kept

    assert (record.attributes[TENANT], record.attributes[TENANT_NAME]) == ("tn_bravo", "bravo")


def test_a_platform_span_carries_the_tenant_once_a_hub_names_it() -> None:
    identity = RunnerIdentityHolder()
    stamp = identity_stamp(identity)

    assert stamp() is None
    identity.hold(_PRE_TENANT)
    assert stamp() == {RUNNER_ID: "rn_01J9ZRUNNER", RUNNER_NAME: "r-claude"}
    identity.hold(_BRAVO)
    assert stamp() == {RUNNER_ID: "rn_01J9ZRUNNER", RUNNER_NAME: "r-claude", TENANT: "tn_bravo", TENANT_NAME: "bravo"}
