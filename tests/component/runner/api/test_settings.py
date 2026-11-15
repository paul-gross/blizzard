"""The runner settings HTTP boundary over the real bound store and parser."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import blizzard.runner.loop_wiring as loop_wiring
from blizzard.auth_core import Role
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.tokens import TokenHash
from blizzard.runner.auth.session import CookieNames, RunnerSession, SessionCookie
from blizzard.runner.cli.runtime import seed_runners
from blizzard.runner.composition import build_host_process
from blizzard.runner.harness.claude_code.section import ClaudeCodeSection
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.leases.model import NewLease
from blizzard.runner.loop_wiring import RunnerStepper
from blizzard.runner.settings_reader import RunnerSettingsReader
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from blizzard.runner.store.internal.settings_store import RunnerSettingsStore
from tests.support.config_inputs import create_runner_app, load_host_config
from tests.support.host_files import TEST_RUNNER_ID, write_minimal_host
from tests.support.runner_client import runner_client, runner_seams
from tests.support.runner_fakes import make_store, make_stores
from tests.support.runner_mint import new_mint


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
        host.config_for_runner(host.runners[0], host.runners[0].own, host.token_for(host.runners[0]))
    )
    seams = runner_seams(app)
    seams.settings_reader = reader
    ready, halted = seed_runners(host, list(host.runners))
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
        missing = client.patch("/api/settings", json={"set": {"agents": 2}})
        assert missing.status_code == 409
        assert missing.json()["code"] == "revision_conflict"
        assert missing.json()["current"] == 2
        response = client.patch("/api/settings", headers={"If-Match": "2"}, json={"set": {"agents": 2}})
        assert response.status_code == 200, response.text
        assert next(r for r in response.json()["settings"] if r["key"] == "agents")["value"] == 2
        assert response.json()["in_force"] == running
        stale = client.patch("/api/settings", headers={"If-Match": "2"}, json={"unset": ["agents"]})
        assert stale.status_code == 409
        assert stale.json()["code"] == "revision_conflict"
        assert stale.json()["current"] == 3
        for key, code in [
            ("bogus", "setting_unknown"),
            ("id", "setting_immutable"),
            ("worker_settings_path", "setting_not_live"),
        ]:
            response = client.patch("/api/settings", headers={"If-Match": "3"}, json={"set": {key: 1}})
            assert response.status_code == 422, response.text
            assert response.json()["code"] == code
        response = client.patch(
            "/api/settings", headers={"If-Match": "3"}, json={"set": {"agents": 1}, "unset": ["agents"]}
        )
        assert response.status_code == 422
        assert response.json()["code"] == "settings_invalid"
        assert response.json()["errors"][0]["key"] == "agents"
        invalid = client.patch("/api/settings", headers={"If-Match": "3"}, json={"set": {"agents": -1}})
        assert invalid.status_code == 422, invalid.text
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
        host.config_for_runner(host.runners[0], host.runners[0].own, host.token_for(host.runners[0])),
        runner_stores=make_stores(base),
    )
    seams = runner_seams(app)
    seams.settings_reader = reader
    ready, halted = seed_runners(host, list(host.runners))
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
        host.config_for_runner(host.runners[0], host.runners[0].own, host.token_for(host.runners[0]))
    )
    ready, halted = seed_runners(host, list(host.runners))
    assert not halted
    graph = build_host_process(host, ready)
    stepper = RunnerStepper(graph.runners[0].config, process_graph=graph.runners[0])
    runner_seams(app).settings_reader = reader
    runner_seams(app).stepper = stepper
    with runner_client(app) as client:
        view = client.get("/api/settings").json()
        row = next(row for row in view["settings"] if row["key"] == key)
        assert row["value"] == expected
        assert row["source"] == source
        assert row["written_by"] == ("seed" if source == "entry" else None)
        response = client.patch("/api/settings", headers={"If-Match": str(view["revision"])}, json={"unset": [key]})
        assert response.status_code == 200, response.text
        row = next(row for row in response.json()["settings"] if row["key"] == key)
        assert row["source"] == "default"
        section = reader.snapshot()[1].harness_sections.of("claude_code")
        assert isinstance(section, ClaudeCodeSection)
        assert section.permission_mode is None
    stepper.close()
    graph.close()
