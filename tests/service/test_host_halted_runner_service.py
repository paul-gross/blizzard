"""A runner the host cannot run is halted, and the host serves on (service tier): a live ``blizzard-runner host``
whose one runner holds a token the mock hub resolves to another runner, or whose entry carries a runner-only config
error, keeps answering ``/api/health`` and names the halt in ``blizzard-runner status``. The mismatched runner is
built and its tick gated, so ``/api/runner``'s identity verdict carries its halt; the misconfigured one is left
unbuilt, so ``/api/ready``'s detail names it. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import contextlib
import dataclasses
import subprocess
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest

from blizzard.foundation.runner_token_names import HOST_TOKEN_VARIABLE, hub_token_variable
from blizzard.runner.config import DEFAULT_WORKSPACE_ID, ProjectBinding
from blizzard.runner.host_config import RunnerEntry
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.internal.token_file import HubTokenFile
from tests.fleet_launchers import REPO, await_http, runner_config, terminate
from tests.host_files import host_config_of
from tests.runner_join import RUNNER_BIN, JoinedRunner, runner_spawn_env, seed_pinned_runner
from tests.service.runner_support import (
    CONFIGURED_RUNNER,
    Added,
    add_host,
    add_project,
    add_runner,
    asserted_chunk_status,
    host_leased,
    host_rows,
    mock_fleet_world,
    single_runner_host,
)
from tests.service.support import (
    mock_hub,
    mock_hub_chunk_spec,
    poll_until,
    require_mock_fleet,
    service_gate,
)
from tests.support import daemon_log_sink, free_port, read_daemon_log

pytestmark = [pytest.mark.service, service_gate]

_OTHER = JoinedRunner(runner_id="rn_01JX00000000000000000HALTB", runner_name="runner-other", token="tok_halt_b")


def _identity_state(local: httpx2.Client) -> str | None:
    view = local.get(f"/runners/{CONFIGURED_RUNNER.runner_id}/api/runner")
    return view.json()["identity"]["state"] if view.status_code == 200 else None


def _status(root: Path, hub_url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(RUNNER_BIN), "status", "--dir", str(root)],
        capture_output=True,
        text=True,
        env=runner_spawn_env(hub_url),
        timeout=60,
        check=False,
    )


def test_a_token_that_resolves_to_another_runner_halts_that_runner_while_the_host_serves(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    hub_port = free_port()
    hub_url = f"http://127.0.0.1:{hub_port}"
    root = tmp_path / "host"
    with mock_hub(bin_dir, hub_port) as hub:
        seed_pinned_runner(hub, CONFIGURED_RUNNER)
        seed_pinned_runner(hub, _OTHER)
        with single_runner_host(root, hub_url, _OTHER.token) as local:
            assert poll_until(lambda: _identity_state(local) == "mismatched", timeout=30.0), read_daemon_log(
                root / "daemon.log"
            )
            assert local.get("/api/health").status_code == 200
            status = _status(root, hub_url)
            said = status.stdout + status.stderr
            assert f"REFUSING: token belongs to runner {_OTHER.runner_id}" in said, said
            assert "BZ_HUB_TOKEN_RUNNER_LOCAL" in said and "[[runner]] id" in said, said
            # The halted runner does no work under the token it holds: it never registers as the runner it names.
            unregistered = hub.get(f"/api/fleet/runners/{_OTHER.runner_id}", headers=_OTHER.bearer)
            assert unregistered.status_code == 409, unregistered.text


def test_a_runner_only_config_error_halts_that_runner_while_the_host_serves(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    hub_port = free_port()
    hub_url = f"http://127.0.0.1:{hub_port}"
    root = tmp_path / "host"
    with mock_hub(bin_dir, hub_port) as hub:
        seed_pinned_runner(hub, CONFIGURED_RUNNER)
        with single_runner_host(root, hub_url, CONFIGURED_RUNNER.token, subscription="missing") as local:
            assert local.get("/api/health").status_code == 200
            detail = local.get("/api/ready").json()["detail"]
            assert "runner-local" in detail and "names subscription 'missing'" in detail, detail
            assert local.get(f"/runners/{CONFIGURED_RUNNER.runner_id}/api/runner").status_code == 503
            status = _status(root, hub_url)
            said = status.stdout + status.stderr
            assert status.returncode != 0
            assert "serves no runner" in said and "names subscription 'missing'" in said, said


def test_a_store_that_names_another_runner_halts_that_runner_while_the_host_serves(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    hub_port = free_port()
    hub_url = f"http://127.0.0.1:{hub_port}"
    root = tmp_path / "host"
    with mock_hub(bin_dir, hub_port) as hub:
        seed_pinned_runner(hub, CONFIGURED_RUNNER)
        with single_runner_host(root, hub_url, CONFIGURED_RUNNER.token, store_names=_OTHER.runner_id) as local:
            assert local.get("/api/health").status_code == 200
            detail = local.get("/api/ready").json()["detail"]
            assert _OTHER.runner_id in detail and CONFIGURED_RUNNER.runner_id in detail, detail
            assert local.get(f"/runners/{CONFIGURED_RUNNER.runner_id}/api/runner").status_code == 503
            # Nothing registered under the configured id: the store still names only the one runner.
            unregistered = hub.get(
                f"/api/fleet/runners/{CONFIGURED_RUNNER.runner_id}", headers=CONFIGURED_RUNNER.bearer
            )
            assert unregistered.status_code == 409, unregistered.text


# --- A host holding a host token, with several runners --------------------------------------------------------------


def _seed_chunk(hub: httpx2.Client, project_id: str) -> str:
    seeded = hub.post("/_seed/chunk", json={**mock_hub_chunk_spec(f"{REPO}/issues/1"), "project_id": project_id})
    assert seeded.status_code == 201, seeded.text
    return str(seeded.json()["chunk_id"])


@contextlib.contextmanager
def _token_host(
    root: Path, bin_dir: Path, hub_port: int, workspace: Path, host_token: str, runners: tuple[Added, ...]
) -> Iterator[httpx2.Client]:
    """``blizzard-runner host`` over ``root`` holding ``host_token``, its host file one ``[[runner]]`` per
    ``runners`` entry — each bound to its own project in the fixture workspace and holding its own token in
    ``.env`` — on the mock harness; yields a client to its port once it answers ``/api/health``."""
    hub_url = f"http://127.0.0.1:{hub_port}"
    base = runner_config(root, workspace, bin_dir, hub_port, join=False)
    base = dataclasses.replace(
        base,
        workspaces=(dataclasses.replace(base.workspaces[0], envs=tuple(f"e{i + 1}" for i in range(len(runners)))),),
    )
    workspace_id = base.workspaces[0].id
    entries = tuple(
        RunnerEntry(
            name=runner.name,
            id=runner.runner_id,
            subscription=base.subscriptions[0].slug,
            bindings=(ProjectBinding(runner.project, workspace_id, 1),),
            own=dataclasses.replace(base, name=runner.name, runner_id=runner.runner_id),
        )
        for runner in runners
    )
    HostLayout(root).config_path.write_text(dataclasses.replace(host_config_of(base), runners=entries).to_toml())
    for runner in runners:
        HubTokenFile.of(root, hub_token_variable(runner.name)).write(runner.token)
    log = root / "daemon.log"
    proc = subprocess.Popen(
        [str(RUNNER_BIN), "host", "--dir", str(root)],
        env=runner_spawn_env(
            hub_url,
            {"BZ_RUNNER_TICK_SECONDS": "0.5", HOST_TOKEN_VARIABLE: host_token, "BLIZZARD_MOCK_HARNESS_FENCE": "1"},
        ),
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx2.Client(base_url=f"http://127.0.0.1:{base.port}", timeout=10.0)
    try:
        await_http(proc, client, "/api/health", log=log)
        yield client
    finally:
        client.close()
        terminate(proc)


def _registration(local: httpx2.Client) -> dict:
    answer = local.get("/api/host/registration")
    assert answer.status_code == 200, answer.text
    return answer.json()


def _runner_identity(local: httpx2.Client, runner_id: str) -> str | None:
    view = local.get(f"/runners/{runner_id}/api/runner")
    return view.json()["identity"]["state"] if view.status_code == 200 else None


def _host_runner(local: httpx2.Client, runner_id: str) -> dict:
    listed = local.get("/api/host/runners")
    assert listed.status_code == 200, listed.text
    return next(item for item in listed.json()["items"] if item["runner_id"] == runner_id)


def _own_view(hub: httpx2.Client, runner: Added) -> dict:
    """The hub's read of ``runner`` as itself, once its first registration has landed."""
    url = f"/api/fleet/runners/{runner.runner_id}"
    assert poll_until(lambda: hub.get(url, headers=runner.bearer).status_code == 200, timeout=30.0)
    return hub.get(url, headers=runner.bearer).json()


def test_a_host_with_a_token_reports_itself_and_records_its_identity(tmp_path: Path) -> None:
    bin_dir, workspace = mock_fleet_world(tmp_path)
    hub_port = free_port()
    root = tmp_path / "host"
    with mock_hub(bin_dir, hub_port) as hub:
        host_id, host_token = add_host(hub)
        add_project(hub, "p-a")
        add_project(hub, "p-b")
        runners = (
            add_runner(hub, "runner-a", "p-a", host_id=host_id),
            add_runner(hub, "runner-b", "p-b", host_id=host_id),
        )
        with _token_host(root, bin_dir, hub_port, workspace, host_token, runners) as local:
            assert poll_until(lambda: _registration(local)["state"] == "registered", timeout=30.0), (
                _registration(local),
                read_daemon_log(root / "daemon.log"),
            )
            registration = _registration(local)
            assert registration["host_id"] == host_id, registration
            assert set(registration["held_runner_ids"]) == {r.runner_id for r in runners}, registration
            identity = host_rows(root, "SELECT host_id FROM host_identity")
            assert identity == [(host_id,)], identity
            record = hub.get("/api/fleet/host", headers={"Authorization": f"Bearer {host_token}"})
            assert record.status_code == 200, record.text
            body = record.json()
            assert body["host_id"] == host_id, body
            assert {r["runner_id"] for r in body["runners"]} == {r.runner_id for r in runners}, body
            assert body["last_seen_at"] is not None, body
            # The mock answers a host's reported workspaces and roster only through the runners under it: a binding
            # the report lacks is flagged unreported, a slug it lacks unrostered — so neither flag means both landed.
            for runner in runners:
                own = _own_view(hub, runner)
                assert [(b["workspace_id"], b["workspace_unreported"]) for b in own["bindings"]] == [
                    (DEFAULT_WORKSPACE_ID, False)
                ], own
                assert (own["subscription"], own["subscription_unrostered"]) == ("anthropic", False), own


def test_two_held_runners_claim_and_lease_independently(tmp_path: Path) -> None:
    bin_dir, workspace = mock_fleet_world(tmp_path)
    hub_port = free_port()
    root = tmp_path / "host"
    with mock_hub(bin_dir, hub_port) as hub:
        host_id, host_token = add_host(hub)
        project_a, project_b = add_project(hub, "p-a"), add_project(hub, "p-b")
        runner_a = add_runner(hub, "runner-a", "p-a", host_id=host_id)
        runner_b = add_runner(hub, "runner-b", "p-b", host_id=host_id)
        chunk_a, chunk_b = _seed_chunk(hub, project_a), _seed_chunk(hub, project_b)
        with _token_host(root, bin_dir, hub_port, workspace, host_token, (runner_a, runner_b)):
            log = root / "daemon.log"
            assert poll_until(
                lambda: (
                    host_leased(root, runner_a.runner_id, chunk_a) and host_leased(root, runner_b.runner_id, chunk_b)
                ),
                timeout=60.0,
            ), read_daemon_log(log)
            # Each runner leased its own project's chunk, never the other's.
            assert not host_leased(root, runner_a.runner_id, chunk_b) and not host_leased(
                root, runner_b.runner_id, chunk_a
            )
            assert poll_until(
                lambda: asserted_chunk_status(hub, chunk_a) == "done" and asserted_chunk_status(hub, chunk_b) == "done",
                timeout=120.0,
            ), (asserted_chunk_status(hub, chunk_a), asserted_chunk_status(hub, chunk_b), read_daemon_log(log))


def test_a_refused_and_a_not_held_runner_halt_alone_while_the_held_runner_works(tmp_path: Path) -> None:
    bin_dir, workspace = mock_fleet_world(tmp_path)
    hub_port = free_port()
    root = tmp_path / "host"
    with mock_hub(bin_dir, hub_port) as hub:
        host_id, host_token = add_host(hub)
        project_a = add_project(hub, "p-a")
        add_project(hub, "p-b")
        add_project(hub, "p-c")
        working = add_runner(hub, "runner-a", "p-a", host_id=host_id)
        refused = add_runner(hub, "runner-b", "p-b", host_id=host_id)
        standalone = add_runner(hub, "runner-c", "p-c")
        revoked = hub.post(f"/_seed/runners/{refused.runner_id}/token-revocations")
        assert revoked.is_success, revoked.text
        first = _seed_chunk(hub, project_a)
        with _token_host(root, bin_dir, hub_port, workspace, host_token, (working, refused, standalone)) as local:
            log = root / "daemon.log"
            assert poll_until(
                lambda: (
                    _runner_identity(local, refused.runner_id) == "refused"
                    and _runner_identity(local, standalone.runner_id) == "not_held"
                ),
                timeout=30.0,
            ), (
                _runner_identity(local, refused.runner_id),
                _runner_identity(local, standalone.runner_id),
                read_daemon_log(log),
            )
            for halted in (refused, standalone):
                view = _host_runner(local, halted.runner_id)
                assert view["state"] == "halted" and view["served"] is True, view
                assert len(view["halted_reasons"]) == 1, view
            assert local.get("/api/health").status_code == 200
            assert poll_until(lambda: asserted_chunk_status(hub, first) == "done", timeout=120.0), read_daemon_log(log)
            # After the halts, the held runner keeps claiming; the halted two never lease anything.
            second = _seed_chunk(hub, project_a)
            assert poll_until(lambda: host_leased(root, working.runner_id, second), timeout=60.0), read_daemon_log(log)
            for halted in (refused, standalone):
                assert not host_rows(root, "SELECT 1 FROM leases WHERE runner_id = ?", halted.runner_id)


def test_a_version_refused_runner_halts_alone_while_its_sibling_keeps_leasing(tmp_path: Path) -> None:
    bin_dir, workspace = mock_fleet_world(tmp_path)
    hub_port = free_port()
    root = tmp_path / "host"
    detail = "runner 0.1.4 is below hub 0.3.0's floor 0.2; upgrade it"
    with mock_hub(bin_dir, hub_port) as hub:
        host_id, host_token = add_host(hub)
        project_a = add_project(hub, "p-a")
        add_project(hub, "p-b")
        working = add_runner(hub, "runner-a", "p-a", host_id=host_id)
        refused = add_runner(hub, "runner-b", "p-b", host_id=host_id)
        lever = hub.post(
            "/_levers/version_refused",
            json={"payload": {"runner_id": refused.runner_id, "reason": "too-old", "detail": detail}},
        )
        assert lever.is_success, lever.text
        first = _seed_chunk(hub, project_a)
        with _token_host(root, bin_dir, hub_port, workspace, host_token, (working, refused)) as local:
            log = root / "daemon.log"
            assert poll_until(lambda: _runner_identity(local, refused.runner_id) == "version_refused", timeout=30.0), (
                _runner_identity(local, refused.runner_id),
                read_daemon_log(log),
            )
            view = _host_runner(local, refused.runner_id)
            assert view["state"] == "halted" and view["halted_reasons"] == [detail], view
            assert poll_until(lambda: host_leased(root, working.runner_id, first), timeout=60.0), read_daemon_log(log)
            assert _runner_identity(local, refused.runner_id) == "version_refused"
            assert not host_rows(root, "SELECT 1 FROM leases WHERE runner_id = ?", refused.runner_id)


def test_against_a_hub_with_no_hosts_both_runners_work_and_the_host_buffers_its_facts(tmp_path: Path) -> None:
    bin_dir, workspace = mock_fleet_world(tmp_path)
    hub_port = free_port()
    root = tmp_path / "host"
    with mock_hub(bin_dir, hub_port) as hub:
        host_id, host_token = add_host(hub)
        project_a, project_b = add_project(hub, "p-a"), add_project(hub, "p-b")
        runner_a = add_runner(hub, "runner-a", "p-a", host_id=host_id)
        runner_b = add_runner(hub, "runner-b", "p-b", host_id=host_id)
        lever = hub.post("/_levers/host_plane_absent", json={})
        assert lever.is_success, lever.text
        chunk_a, chunk_b = _seed_chunk(hub, project_a), _seed_chunk(hub, project_b)
        with _token_host(root, bin_dir, hub_port, workspace, host_token, (runner_a, runner_b)) as local:
            log = root / "daemon.log"
            assert poll_until(lambda: _registration(local)["state"] == "no_hosts", timeout=30.0), (
                _registration(local),
                read_daemon_log(log),
            )
            assert poll_until(
                lambda: (
                    host_leased(root, runner_a.runner_id, chunk_a) and host_leased(root, runner_b.runner_id, chunk_b)
                ),
                timeout=60.0,
            ), read_daemon_log(log)
            assert poll_until(
                lambda: bool(host_rows(root, "SELECT 1 FROM host_outbound_buffer WHERE acked_at IS NULL")), timeout=30.0
            ), read_daemon_log(log)
            registration = _registration(local)
            assert registration["state"] == "no_hosts" and registration["buffered_facts"] >= 1, registration
            assert not host_rows(root, "SELECT 1 FROM host_identity")
