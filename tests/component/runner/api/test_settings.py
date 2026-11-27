"""The runner settings HTTP boundary over the real bound store and parser."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest

import blizzard.runner.loop_wiring as loop_wiring
from blizzard.auth_core import Role
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.tokens import TokenHash
from blizzard.runner.api.settings import SettingsProblem
from blizzard.runner.auth.session import CookieNames, RunnerSession, SessionCookie
from blizzard.runner.cli.runtime import seed_runners
from blizzard.runner.composition import build_host_process
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.harness.claude_code.section import ClaudeCodeSection
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.leases.model import NewLease
from blizzard.runner.loop_wiring import RunnerStepper
from blizzard.runner.settings.authoring import RunnerSettingsAuthoring
from blizzard.runner.settings_reader import RunnerSettingsReader
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from blizzard.runner.store.internal.settings_store import RunnerSettingsStore
from tests.support.config_inputs import create_runner_app, load_host_config
from tests.support.host_files import TEST_RUNNER_ID, UnresolvableSettings, write_minimal_host
from tests.support.runner_client import runner_client, runner_seams
from tests.support.runner_fakes import make_store, make_stores
from tests.support.runner_mint import new_mint


def test_first_registration_seeds_settings_for_http_without_another_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from blizzard.foundation.settings_record.vocabulary import Entry
    from blizzard.runner.lifecycle.registration import Registration
    from tests.support.runner_fakes import FakeHub

    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    monkeypatch.setenv("BZ_HUB_TOKEN_SIBLING", "test-token")
    monkeypatch.setenv("BZ_HOST_TOKEN", "host-token")
    url = f"sqlite:///{tmp_path / 'host.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"', runner="agents = 3\n")
    with (tmp_path / "blizzard-host.toml").open("a") as config:
        config.write('\n[[runner]]\nid = "rn_sibling"\nname = "sibling"\nprojects = []\n')
    host = load_host_config(tmp_path)
    base = make_store(url, TEST_RUNNER_ID)
    sibling = make_store(url, "rn_sibling")
    RunnerIdentityStore(sibling.connections).record_runner_identity(
        RunnerIdentity("rn_sibling", "sibling", datetime.now(UTC))
    )
    ready, halted = seed_runners(host, clock=SystemClock())
    assert len(ready) == 2 and not halted
    graph = build_host_process(host, ready)
    runner = graph.runners[0]
    stepper = RunnerStepper(runner.config, process_graph=runner)
    app = create_runner_app(runner.config)
    seams = runner_seams(app)
    seams.settings_reader = runner.settings_reader
    seams.stepper = stepper
    hub = FakeHub(default_runner_id=TEST_RUNNER_ID)

    def register(ctx: loop_wiring.LoopContext) -> None:
        Registration(replace(ctx, hub=hub)).run()

    monkeypatch.setattr(loop_wiring, "tick", register)
    try:
        with runner_client(app) as client:
            before = client.get("/api/settings")
            assert before.status_code == 200, before.text
            preview = before.json()
            assert preview["settings_error"] is None
            preview_agents = next(row for row in preview["settings"] if row["key"] == "agents")
            assert preview_agents["value"] == 3
            assert preview_agents["source"] == "default"
            assert preview_agents["revision"] is None
            assert preview_agents["written_at"] is None
            assert preview_agents["written_by"] is None
            assert RunnerIdentityStore(base.connections).runner_identity() is None
            assert runner.stores.settings.snapshot().entries == {}
            unresolvable = UnresolvableSettings(monkeypatch)
            unresolvable.hold()
            unavailable = client.get("/api/settings")
            assert unavailable.status_code == 200, unavailable.text
            assert unavailable.json()["revision"] == preview["revision"]
            assert unavailable.json()["settings"] == []
            assert unavailable.json()["settings_error"]
            assert runner.stores.settings.snapshot().entries == {}
            unresolvable.release()
            stepper.request()
            assert RunnerIdentityStore(base.connections).runner_identity() is not None
            assert runner.stores.settings.snapshot().entries == {}
            response = client.get("/api/settings")
            assert response.status_code == 200, response.text
            rows = {row["key"]: row for row in response.json()["settings"]}
            assert rows["agents"]["value"] == 3
            assert runner.stores.settings.snapshot().entries["agents"] == Entry(3)
            assert runner.stores.settings.snapshot().entries["projects"] == Entry([])
    finally:
        stepper.close()
        graph.close()


def test_settings_writes_are_revision_conditioned_and_history_is_paged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'host.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"')
    host = load_host_config(tmp_path)
    base = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(base.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host.runners[0].name, datetime.now(UTC))
    )
    store = RunnerSettingsStore(base.connections, SystemClock())
    reader = RunnerSettingsReader(host, host.runners[0], store)
    assert host.runners[0].authored is not None
    assert host.runners[0].own is not None
    store.seed(host.runners[0].authored, reader.seed_keys({}), validate=reader.validate)
    app = create_runner_app(
        host.config_for_runner(host.runners[0], host.runners[0].own, host.token_for(host.runners[0]).value)
    )
    seams = runner_seams(app)
    seams.settings_reader = reader
    seams.settings_authoring = RunnerSettingsAuthoring(reader, store, HOST_REGISTRY)
    ready, halted = seed_runners(host, clock=SystemClock())
    assert not halted
    graph = build_host_process(host, ready)
    stepper = RunnerStepper(graph.runners[0].config, process_graph=graph.runners[0])
    seams.stepper = stepper

    def no_tick(ctx: loop_wiring.LoopContext) -> None:
        pass

    monkeypatch.setattr(loop_wiring, "tick", no_tick)
    with runner_client(app) as client:
        first = client.get("/api/settings")
        assert first.status_code == 200, first.text
        assert first.json()["revision"] == 2
        rows = {row["key"]: row for row in first.json()["settings"]}
        assert rows["agents"]["value"] == 0
        assert rows["agents"]["source"] == "default"
        assert rows["harness.autonomy"]["value"] == "dangerous"
        assert rows["cost.window_hours"]["value"] == 24.0
        assert rows["context.sample_interval_seconds"]["value"] == 60
        assert rows["worker.shell"]["value"]
        assert rows["models.aliases"]["value"] == {}
        assert first.json()["in_force"]["revision"] is None
        stepper.request()
        running = client.get("/api/settings").json()["in_force"]
        assert running["revision"] == 2 and running["since"]
        spec = app.openapi()
        responses = spec["paths"]["/runners/{runner_id}/api/settings"]["patch"]["responses"]
        problem_schema = {"$ref": "#/components/schemas/SettingsProblem"}
        assert responses["409"]["content"] == {"application/problem+json": {"schema": problem_schema}}
        assert responses["422"]["content"] == {
            "application/problem+json": {"schema": problem_schema},
            "application/json": {"schema": {"$ref": "#/components/schemas/HTTPValidationError"}},
        }
        required = set(spec["components"]["schemas"]["SettingsProblem"]["required"])

        def assert_problem(response: httpx2.Response) -> None:
            # All problem fields, including nulls, are part of the wire schema.
            assert response.headers["content-type"] == "application/problem+json"
            body = response.json()
            assert required <= body.keys()
            SettingsProblem.model_validate(body)

        malformed = client.patch("/api/settings", content="{", headers={"content-type": "application/json"})
        assert malformed.status_code == 422
        assert malformed.headers["content-type"] == "application/json"
        assert malformed.json()["detail"][0]["type"] == "json_invalid"
        wrong_body = client.patch("/api/settings", json={"unset": "agents"})
        assert wrong_body.status_code == 422
        assert wrong_body.headers["content-type"] == "application/json"
        assert wrong_body.json()["detail"][0]["loc"] == ["body", "unset"]
        missing = client.patch("/api/settings", json={"set": {"agents": 2}})
        assert missing.status_code == 409
        assert_problem(missing)
        assert missing.json()["key"] is None
        assert missing.json()["errors"] is None
        assert missing.json()["code"] == "revision_conflict"
        assert missing.json()["current"] == 2
        response = client.patch("/api/settings", headers={"If-Match": "2"}, json={"set": {"agents": 2}})
        assert response.status_code == 200, response.text
        assert next(r for r in response.json()["settings"] if r["key"] == "agents")["value"] == 2
        assert response.json()["in_force"] == running
        stale = client.patch("/api/settings", headers={"If-Match": "2"}, json={"unset": ["agents"]})
        assert stale.status_code == 409
        assert_problem(stale)
        assert stale.json()["code"] == "revision_conflict"
        assert stale.json()["current"] == 3
        for key, code in [
            ("bogus", "setting_unknown"),
            ("id", "setting_immutable"),
            ("worker_settings_path", "setting_not_live"),
        ]:
            response = client.patch("/api/settings", headers={"If-Match": "3"}, json={"set": {key: 1}})
            assert response.status_code == 422, response.text
            assert_problem(response)
            assert response.json()["code"] == code
        response = client.patch(
            "/api/settings", headers={"If-Match": "3"}, json={"set": {"agents": 1}, "unset": ["agents"]}
        )
        assert response.status_code == 422
        assert_problem(response)
        assert response.json()["code"] == "settings_invalid"
        assert response.json()["errors"][0]["key"] == "agents"
        invalid = client.patch("/api/settings", headers={"If-Match": "3"}, json={"set": {"agents": -1}})
        assert invalid.status_code == 422, invalid.text
        assert_problem(invalid)
        assert invalid.json()["code"] == "settings_invalid"
        assert [error["key"] for error in invalid.json()["errors"]] == ["agents"]
        unchanged = client.get("/api/settings").json()
        assert unchanged["revision"] == 3
        assert next(r for r in unchanged["settings"] if r["key"] == "agents")["value"] == 2
        assert client.get("/api/settings/changes").json()["changes"][0]["revision"] == 3
        response = client.patch("/api/settings", headers={"If-Match": "3"}, json={"unset": ["agents"]})
        assert response.status_code == 200, response.text
        page = client.get("/api/settings/changes?limit=1").json()
        assert page["changes"][0]["revision"] == 4
        assert (
            client.get(f"/api/settings/changes?limit=1&before={page['next_before']}").json()["changes"][0]["revision"]
            == 3
        )
        response = client.patch(
            "/api/settings", headers={"If-Match": "4"}, json={"set": {"runner_prompt_file": "missing-prompt"}}
        )
        assert response.status_code == 200
        stepper.request()
        failed = client.get("/api/settings").json()["in_force"]
        assert failed["revision"] == 2
        assert failed["failed"]["revision"] == 5
        assert "missing-prompt" in failed["failed"]["error"]
        assert failed["failed"]["since"]
        response = client.patch("/api/settings", headers={"If-Match": "5"}, json={"unset": ["runner_prompt_file"]})
        assert response.status_code == 200
        stepper.request()
        recovered = client.get("/api/settings").json()["in_force"]
        assert recovered["revision"] == 6 and recovered["failed"] is None

        response = client.patch("/api/settings", headers={"If-Match": "6"}, json={"set": {"agents": 2}})
        assert response.status_code == 200
        unresolvable = UnresolvableSettings(monkeypatch)
        unresolvable.hold()
        stepper.request()
        response = client.get("/api/settings")
        assert response.status_code == 200, response.text
        failed_view = response.json()
        assert failed_view["revision"] == 7
        assert failed_view["in_force"]["revision"] == 6
        assert failed_view["in_force"]["failed"]["revision"] == 7
        assert failed_view["in_force"]["failed"]["since"]
        assert failed_view["settings"] == []
        assert failed_view["settings_error"]
        invalid = client.patch("/api/settings", headers={"If-Match": "7"}, json={"set": {"agents": -1}})
        assert invalid.status_code == 422, invalid.text
        assert invalid.json()["code"] == "settings_invalid"
        invalid = client.patch(
            "/api/settings", headers={"If-Match": "7"}, json={"set": {"subscription": "not-declared"}}
        )
        assert invalid.status_code == 422, invalid.text
        assert invalid.json()["code"] == "settings_invalid"
        assert client.get("/api/settings").json()["revision"] == 7
        response = client.patch("/api/settings", headers={"If-Match": "7"}, json={"set": {"agents": 1}})
        assert response.status_code == 200
        assert response.json()["revision"] == 8
        assert response.json()["settings_error"]
        unresolvable.release()
        stepper.request()
        recovered_view = client.get("/api/settings").json()
        assert recovered_view["in_force"]["revision"] == 8
        assert recovered_view["in_force"]["failed"] is None
        assert recovered_view["settings_error"] is None
        assert next(r for r in recovered_view["settings"] if r["key"] == "agents")["value"] == 1

    stepper.close()
    graph.close()


@pytest.mark.parametrize("gated", [False, True])
def test_settings_human_roles_and_worker_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gated: bool
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'host.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"')
    host = load_host_config(tmp_path)
    base = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(base.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host.runners[0].name, datetime.now(UTC))
    )
    store = RunnerSettingsStore(base.connections, SystemClock())
    reader = RunnerSettingsReader(host, host.runners[0], store)
    assert host.runners[0].authored is not None
    assert host.runners[0].own is not None
    store.seed(host.runners[0].authored, reader.seed_keys({}), validate=reader.validate)
    now = datetime.now(UTC)
    base.record_mint(
        new_mint(
            NewLease(
                lease_id="lease_1",
                chunk_id="ch_1",
                graph_id="gr_1",
                node_id="nd_1",
                node_name="build",
                epoch=1,
                retries_max=2,
                created_at=now,
            ),
            token_hash=TokenHash("worker-token").hex,
        )
    )
    app = create_runner_app(
        host.config_for_runner(host.runners[0], host.runners[0].own, host.token_for(host.runners[0]).value),
        runner_stores=make_stores(base),
    )
    seams = runner_seams(app)
    seams.settings_reader = reader
    seams.settings_authoring = RunnerSettingsAuthoring(reader, store, HOST_REGISTRY)
    ready, halted = seed_runners(host, clock=SystemClock())
    assert not halted
    graph = build_host_process(host, ready)
    stepper = RunnerStepper(graph.runners[0].config, process_graph=graph.runners[0])
    seams.stepper = stepper
    app.state.hub_auth_mode = SimpleNamespace(enabled=lambda: gated)
    names = CookieNames(TEST_RUNNER_ID)
    app.state.federation = SimpleNamespace(cookie_names=lambda: names)
    secret = b"test-settings-session-secret"
    app.state.session_secret = secret
    with runner_client(app) as client:
        for headers in ({"X-Blizzard-Lease-Token": "worker-token"}, {"Authorization": "Bearer worker-token"}):
            heartbeat = client.post("/api/heartbeat", headers=headers, json={"lease_id": "lease_1"})
            assert heartbeat.status_code == 200, heartbeat.text
            for method, path in [
                ("get", "/api/settings"),
                ("get", "/api/settings/changes"),
                ("patch", "/api/settings"),
            ]:
                response = client.request(
                    method, path, headers={**headers, "If-Match": "2"}, json={"set": {"agents": 2}}
                )
                assert response.status_code == 401, response.text
        if not gated:
            assert client.get("/api/settings").json()["revision"] == 2
            assert client.get("/api/settings/changes").status_code == 200
            response = client.patch("/api/settings", headers={"If-Match": "2"}, json={"set": {"agents": 2}})
            assert response.status_code == 200, response.text
        for role in (Role.CONTRIBUTOR, Role.SUPERUSER) if gated else ():
            session = RunnerSession("alice", role, now, now + timedelta(hours=1))
            client.cookies.set(names.session, SessionCookie(secret).mint(session))
            assert client.get("/api/settings").status_code == 200
            assert client.get("/api/settings/changes").status_code == 200
            response = client.patch("/api/settings", headers={"If-Match": "2"}, json={"set": {"agents": 2}})
            assert response.status_code == (403 if role is Role.CONTRIBUTOR else 200), response.text
            if role is Role.CONTRIBUTOR:
                assert response.json()["code"] == "superuser_required"

    # A socket peer retains implicit operator authority even with federation enabled.
    with runner_client(app, client=None) as client:
        revision = client.get("/api/settings").json()["revision"]
        response = client.patch("/api/settings", headers={"If-Match": str(revision)}, json={"set": {"agents": 3}})
        assert response.status_code == 200, response.text

    stepper.close()
    graph.close()


@pytest.mark.parametrize(
    ("legacy", "key", "expected", "source"),
    [
        ('harness_binary = "legacy-claude"', "claude_code.binary", "legacy-claude", "entry"),
        ('harness_permission_mode = "acceptEdits"', "harness.autonomy", "dangerous", "default"),
        ('harness_permission_mode = ""', "harness.autonomy", "dangerous", "default"),
    ],
)
def test_legacy_entry_projects_provenance_and_can_be_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy: str, key: str, expected: str, source: str
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'host.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"', runner=legacy)
    host = load_host_config(tmp_path)
    base = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(base.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host.runners[0].name, datetime.now(UTC))
    )
    store = RunnerSettingsStore(base.connections, SystemClock())
    reader = RunnerSettingsReader(host, host.runners[0], store)
    assert host.runners[0].authored is not None
    assert host.runners[0].own is not None
    store.seed(host.runners[0].authored, reader.seed_keys({}), validate=reader.validate)
    app = create_runner_app(
        host.config_for_runner(host.runners[0], host.runners[0].own, host.token_for(host.runners[0]).value)
    )
    ready, halted = seed_runners(host, clock=SystemClock())
    assert not halted
    graph = build_host_process(host, ready)
    stepper = RunnerStepper(graph.runners[0].config, process_graph=graph.runners[0])
    runner_seams(app).settings_reader = reader
    runner_seams(app).settings_authoring = RunnerSettingsAuthoring(reader, store, HOST_REGISTRY)
    runner_seams(app).stepper = stepper
    with runner_client(app) as client:
        view = client.get("/api/settings").json()
        row = next(row for row in view["settings"] if row["key"] == key)
        assert row["value"] == expected
        assert row["source"] == source
        assert row["written_by"] == ("seed" if source == "entry" else None)
        if key == "claude_code.binary":
            assert row["revision"] is not None
            assert row["written_at"]
            override = client.patch(
                "/api/settings",
                headers={"If-Match": str(view["revision"])},
                json={"set": {key: "canonical-claude"}},
            )
            assert override.status_code == 200, override.text
            view = override.json()
            row = next(row for row in view["settings"] if row["key"] == key)
            assert (row["value"], row["source"], row["revision"], row["written_by"]) == (
                "canonical-claude",
                "entry",
                view["revision"],
                "operator",
            )
        response = client.patch("/api/settings", headers={"If-Match": str(view["revision"])}, json={"unset": [key]})
        assert response.status_code == 200, response.text
        row = next(row for row in response.json()["settings"] if row["key"] == key)
        assert row["source"] == "default"
        assert row["revision"] == response.json()["revision"]
        assert row["written_by"] == "operator"
        assert row["written_at"]
        if key == "claude_code.binary":
            assert row["value"] != "legacy-claude"
            assert row["value"] != "canonical-claude"
        section = reader.snapshot()[1].harness_sections.of("claude_code")
        assert isinstance(section, ClaudeCodeSection)
        assert section.permission_mode is None
    stepper.close()
    graph.close()
