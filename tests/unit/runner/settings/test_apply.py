"""The host settings document's planner, by value: what it writes, what it refuses, and what it never touches."""

from __future__ import annotations

import pytest

from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.host_records.model import HostRecord, RecordKind
from blizzard.runner.settings.apply import (
    ApplyInvalid,
    ApplyRefusal,
    HeldHostState,
    HeldRunner,
    export_host_settings,
    plan_host_settings,
    stated_runners,
)
from blizzard.runner.settings.ports import SettingsSnapshot

_WORKSPACE = HostRecord(RecordKind.WORKSPACE, "ws_1", 2, {"root": "/w"})
_RETIRED = HostRecord(RecordKind.WORKSPACE, "ws_old", 4, {"root": "/o"}, retired=True)
_SUBSCRIPTION = HostRecord(RecordKind.SUBSCRIPTION, "max", 1, {"provider": "anthropic", "name": "Max"})
_STATE = HeldHostState(
    host=SettingsSnapshot(3, {"tick_seconds": Entry(5), "tracing.batch_limit": Entry(10)}),
    runners=(HeldRunner("rn_1", SettingsSnapshot(7, {"agents": Entry(1), "name": Entry("r")})),),
    workspaces=(_WORKSPACE, _RETIRED),
    subscriptions=(_SUBSCRIPTION,),
)


def _ops(document: dict[str, object]) -> list[tuple[str, str, str]]:
    return [(o.kind, o.key, o.op) for o in plan_host_settings(document, _STATE, HOST_REGISTRY).outcomes]


def test_an_omitted_record_is_never_touched_or_retired() -> None:
    plan = plan_host_settings({"tick_seconds": 5}, _STATE, HOST_REGISTRY)
    assert (plan.host, plan.records, plan.runners) == (None, (), ())
    assert [(o.key, o.op) for o in plan.outcomes] == [("tick_seconds", "unchanged")]
    assert not plan.writes()
    assert {record.key for record in plan.records_after.workspaces} == {"ws_1"}
    assert {record.key for record in plan.records_after.retired_workspaces} == {"ws_old"}


def test_each_entry_is_planned_in_reconcile_order_with_its_op() -> None:
    plan = plan_host_settings(
        {
            "tracing": {"batch_limit": 20, "worker_program_services": {"claude": "svc"}},
            "runner": [{"id": "rn_1", "agents": 2, "name": "r"}],
            "subscription": [{"slug": "max", "provider": "anthropic", "name": "Max"}],
            "workspace": [{"id": "ws_1", "root": "/x"}, {"id": "ws_old"}, {"id": "ws_new", "root": "/n"}],
        },
        _STATE,
        HOST_REGISTRY,
    )
    assert [(o.kind, o.key, o.op) for o in plan.outcomes] == [
        ("host", "tracing.batch_limit", "edit"),
        ("host", "tracing.worker_program_services", "edit"),
        ("workspace", "ws_1", "edit"),
        ("workspace", "ws_old", "enable"),
        ("workspace", "ws_new", "create"),
        ("subscription", "max", "unchanged"),
        ("runner", "rn_1", "edit"),
    ]
    assert plan.host is not None and dict(plan.host.sets) == {
        "tracing.batch_limit": 20,
        "tracing.worker_program_services": {"claude": "svc"},
    }
    assert [(w.record.key, w.op, w.prior) for w in plan.records] == [
        ("ws_1", "edit", 2),
        ("ws_old", "enable", 4),
        ("ws_new", "create", 0),
    ]
    assert [(w.runner_id, dict(w.change.sets), w.if_match) for w in plan.runners] == [("rn_1", {"agents": 2}, 7)]
    assert plan.entries["tracing.batch_limit"] == Entry(20)
    assert {record.key for record in plan.records_after.workspaces} == {"ws_1", "ws_old", "ws_new"}


@pytest.mark.parametrize("runner_id", ["rn_unknown", "rn_retired"])
def test_a_runner_the_host_does_not_hold_is_refused(runner_id: str) -> None:
    with pytest.raises(ApplyRefusal) as refused:
        plan_host_settings({"runner": [{"id": runner_id, "agents": 2}]}, _STATE, HOST_REGISTRY)
    assert (refused.value.code, refused.value.record_kind, refused.value.record_key) == (
        "runner_unknown",
        "runner",
        runner_id,
    )


def test_an_equal_immutable_key_names_the_record_and_an_unequal_one_is_refused() -> None:
    assert _ops({"subscription": [{"slug": "max", "provider": "anthropic"}]}) == [("subscription", "max", "unchanged")]
    with pytest.raises(ApplyRefusal) as refused:
        plan_host_settings({"subscription": [{"slug": "max", "provider": "openai"}]}, _STATE, HOST_REGISTRY)
    assert (refused.value.code, refused.value.key, refused.value.record_key) == ("setting_immutable", "provider", "max")


@pytest.mark.parametrize(
    ("document", "code", "kind"),
    [
        ({"port": 1}, "setting_not_live", "host"),
        ({"auth": {"superuser": "x"}}, "setting_not_live", "host"),
        ({"bogus": 1}, "setting_unknown", "host"),
        ({"runner": [{"id": "rn_1", "bogus": 1}]}, "setting_unknown", "runner"),
    ],
)
def test_a_boot_or_unknown_key_is_refused(document: dict[str, object], code: str, kind: str) -> None:
    with pytest.raises(ApplyRefusal) as refused:
        plan_host_settings(document, _STATE, HOST_REGISTRY)
    assert (refused.value.code, refused.value.record_kind) == (code, kind)


@pytest.mark.parametrize(
    "document", [{"runner": {"id": "rn_1"}}, {"runner": [{"agents": 1}]}, {"workspace": [{"id": " "}]}]
)
def test_a_malformed_document_is_invalid_naming_the_path(document: dict[str, object]) -> None:
    with pytest.raises(ApplyInvalid) as invalid:
        plan_host_settings(document, _STATE, HOST_REGISTRY)
    assert invalid.value.errors


def test_an_export_states_only_what_is_not_at_its_default_and_applies_back_unchanged() -> None:
    state = HeldHostState(
        host=SettingsSnapshot(3, {"tick_seconds": Entry(None, at_default=True), "tracing.batch_limit": Entry(10)}),
        runners=(HeldRunner("rn_1", SettingsSnapshot(7, {"agents": Entry(1), "harness.autonomy": Entry("full")})),),
        workspaces=(_WORKSPACE, _RETIRED),
        subscriptions=(_SUBSCRIPTION,),
    )
    document = export_host_settings(state.host, state.runners, state.workspaces, state.subscriptions)
    assert document == {
        "tracing": {"batch_limit": 10},
        "workspace": [{"id": "ws_1", "root": "/w"}],
        "subscription": [{"slug": "max", "provider": "anthropic", "name": "Max"}],
        "runner": [{"id": "rn_1", "agents": 1, "harness": {"autonomy": "full"}}],
    }
    plan = plan_host_settings(document, state, HOST_REGISTRY)
    assert not plan.writes()


def test_the_stated_runners_are_the_ids_the_runner_tables_name() -> None:
    assert stated_runners({"runner": [{"id": "rn_b"}, {"id": "rn_a"}, {"agents": 1}, 3]}) == ("rn_a", "rn_b")
    assert stated_runners({"runner": {"id": "rn_a"}}) == ()
