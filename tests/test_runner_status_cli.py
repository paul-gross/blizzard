"""``blizzard runner status`` — the machine-local view.

Driven against a live daemon on a real unix socket: a real server, a real store, and
the CLI wired together, doubled only at the hub seam — the verb is a pure client of
the local API and renders fully with the hub unreachable, which a stubbed transport
would assert nothing about."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn
from click.testing import CliRunner

from blizzard.foundation.runner_identity_state import IdentityState
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.app import build_hosted_app
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.cli.daemon import RunnerDaemon
from blizzard.runner.composition import build_host_process
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.host_config import BuiltRunner, HaltedRunner
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.hub.served_projects import ServedProject
from blizzard.runner.leases.model import NewLease
from blizzard.runner.listeners import Listeners, Uds
from blizzard.runner.subscriptions.credential_renewer import (
    RenewalFailureReason,
    RenewalOutcome,
    RenewalOutcomeKind,
)
from tests.config_inputs import load_host_config
from tests.host_files import load_runner_config
from tests.runner_client import runner_seams
from tests.runner_fakes import SqlAlchemyRunnerStore, runner_store_errors
from tests.runner_init_fakes import init_runner

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _store(root: Path, runner_id: str | None = None) -> SqlAlchemyRunnerStore:
    """The host's store, bound to ``runner_id`` — by default the runner its host file configures."""
    config = load_runner_config(root)
    return SqlAlchemyRunnerStore(
        create_engine_from_url(config.db_url), runner_store_errors(), runner_id=runner_id or config.runner_id
    )


def _registered_store(root: Path) -> SqlAlchemyRunnerStore:
    """The store with an identity row recorded — the local API reads only a registered runner's rows."""
    store = _store(root)
    store.record_runner_identity(
        RunnerIdentity(runner_id=store.connections.runner_id or "", runner_name="runner-local", registered_at=_NOW)
    )
    return store


def _configure_runner_id(root: Path, runner_id: str) -> None:
    """Point the host file's runner at ``runner_id``, as an operator renames the hub id a runner is configured with."""
    config = load_runner_config(root)
    _edit_host_file(root, f'id = "{config.runner_id}"', f'id = "{runner_id}"')


def _edit_host_file(root: Path, old: str, new: str) -> None:
    """Replace ``old`` with ``new`` in the host directory's host file, which must hold it."""
    path = root / "blizzard-host.toml"
    text = path.read_text()
    assert old in text, f"{old!r} is not in the scaffolded host file:\n{text}"
    path.write_text(text.replace(old, new))


def _init_runner(tmp_path: Path) -> Path:
    root = tmp_path / "runner"
    result = init_runner().invoke(runner_group, ["init", str(root)])
    assert result.exit_code == 0, result.output
    # These fixtures seed winter-style /ws/e* workdirs, rather than the new basic
    # provider's per-chunk folders. Pin the provider so takeover cwd stays /ws/e*.
    _edit_host_file(root, 'provider = "basic"', 'provider = "winter"')
    return root


@contextmanager
def _serve_local_api(
    root: Path,
    *,
    trace_status: bool = True,
    refusing: tuple[str, str] | None = None,
    version_refused: str | None = None,
) -> Iterator[tuple[Path, str]]:
    config = load_runner_config(root, port=0)
    app = build_hosted_app(config).app
    if refusing is not None:  # (store runner, token runner): the verdict the loop's check would hold
        runner_seams(app).identity_verdict.record(
            IdentityState.MISMATCHED,
            store_runner_id=refusing[0],
            token_runner_id=refusing[1],
            checked_at=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
        )
    if version_refused is not None:
        runner_seams(app).identity_verdict.refuse_version(version_refused)
    if not trace_status:
        runner_seams(app).trace_status = None  # `GET /api/traces/status` then refuses with a 503
    sockets = Listeners.of(config).bound()
    tcp_url = f"http://{sockets[1].getsockname()[0]}:{sockets[1].getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=lambda: server.run(sockets=sockets), daemon=True)
    thread.start()
    try:
        _await_socket(config.socket_path)
        yield config.socket_path, tcp_url
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)
        Uds(config.socket_path).unlink()


def _await_socket(path: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    transport = httpx.HTTPTransport(uds=str(path))
    with httpx.Client(transport=transport, base_url="http://runner") as client:
        while time.monotonic() < deadline:
            try:
                if client.get("/api/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
    raise AssertionError(f"runner local API never came up on {path}")


def _no_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if the CLI verb reaches for the hub — it must never."""

    def explode(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("`runner status` contacted the hub; it must be a pure client of the local API")

    monkeypatch.setattr(httpx, "post", explode)


@pytest.mark.component
def test_status_renders_the_full_view_with_the_hub_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    store = _registered_store(root)
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="which branch?",
        options=["main", "dev"],
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        asked_at=_NOW,
    )

    # A second, already-escalated chunk with its takeover command.
    store.record_lease(
        NewLease(
            lease_id="lease_2",
            chunk_id="ch_2",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            retries_max=2,
            # The session stamps — so `status` renders which lineage parked
            # and the resume command an operator can paste lands in its configuration.
            session_name="code",
            resolved_model="opus",
            resolved_effort="high",
            created_at=_NOW,
        )
    )
    store.record_spawn(
        "lease_2",
        pid=200,
        process_start_time="start-200",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-b"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id="ch_2", environment_id="e2", workdir="/ws/e2", bound_at=_NOW)
    store.record_closure(
        lease_id="lease_2",
        chunk_id="ch_2",
        node_id="nd_build",
        reason="escalated",
        closed_at=_NOW + timedelta(minutes=1),
    )

    # A third chunk under an open operator takeover — status renders regardless of
    # how the takeover got left open.
    store.record_binding(chunk_id="ch_3", environment_id="e3", workdir="/ws/e3", bound_at=_NOW)
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_3",
        lease_id=None,
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-c"),
        workdir="/ws/e3",
        fence_epoch=None,
        opened_at=_NOW + timedelta(minutes=2),
    )

    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    out = result.output
    assert "runner runner-local" in out
    assert "running" in out  # not paused
    assert "capacity: 1/1 used" in out  # only lease_1 is active; lease_2 closed
    assert "default (unresolved) -> workspace-local: 1/1 used, 0 free" in out
    assert "hub: unreachable" in out  # never synced — honest, not assumed
    assert "held environments (3):" in out
    assert "e1" in out and "e2" in out and "e3" in out
    assert "open asks (1):" in out
    assert "which branch?" in out
    assert "escalations (1):" in out
    assert "ch_2" in out
    # The literal takeover command, carrying the parked session's own configuration
    # rather than whatever a fresh resolution would produce now.
    assert "resume: cd /ws/e2 && claude --resume sess-b --model opus --effort high" in out
    # The wrapped verb leads the raw string, composed from the chunk id and the runner dir alone.
    assert f"takeover: blizzard runner takeover ch_2 --dir {root}" in out
    assert out.index("takeover: blizzard runner takeover ch_2") < out.index("resume: cd /ws/e2")
    assert "BLIZZARD_LEASE_TOKEN" not in out
    assert "session=code (opus, high)" in out  # which lineage parked, not just its id
    assert "open takeovers (1):" in out
    assert "chunk ch_3" in out and "takeover=tko_1" in out
    assert "subscriptions (1):" in out
    assert "\nharness telemetry: off\n" in out
    assert "anthropic (anthropic): never sampled" in out


@pytest.mark.component
def test_status_renders_empty_sections_on_a_fresh_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert "leases (0):" in result.output
    assert "held environments (0):" in result.output
    assert "open asks (0):" in result.output
    assert "escalations (0):" in result.output
    assert "open takeovers (0):" in result.output
    assert "subscriptions (1):" in result.output  # the synthesized legacy anthropic declaration
    assert "never sampled" in result.output
    assert "gates: none" in result.output
    assert "identity: unchecked" in result.output
    assert "REFUSING" not in result.output


@pytest.mark.component
def test_status_headline_names_no_tenant_before_a_hub_names_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    _configure_runner_id(root, "rn_1")
    _store(root).record_runner_identity(RunnerIdentity(runner_id="rn_1", runner_name="r-one", registered_at=_NOW))
    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    headline = result.output.splitlines()[0]
    assert headline.startswith("runner r-one (rn_1)  workspace="), headline
    assert "tenant=" not in result.output


@pytest.mark.component
def test_status_headline_names_the_tenant_beside_the_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    _configure_runner_id(root, "rn_1")
    _store(root).record_runner_identity(
        RunnerIdentity(
            runner_id="rn_1", runner_name="r-one", registered_at=_NOW, tenant_id="tn_bravo", tenant_name="bravo"
        )
    )
    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0].startswith("runner r-one (rn_1)  tenant=bravo (tn_bravo)  workspace=")


@pytest.mark.component
def test_status_names_the_served_projects_unresolved_and_not_yet_answered_with_the_hub_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_runner(tmp_path)
    _edit_host_file(
        root,
        'projects = [{ project = "default", workspace = "workspace-local", agents = 1 }]',
        'projects = [{ project = "blizzard", workspace = "workspace-local", agents = 1 }, '
        '{ project = "old-slug", workspace = "workspace-local", agents = 1 }, '
        '{ project = "blizzard-context", workspace = "workspace-local", agents = 1 }]',
    )
    _no_hub(monkeypatch)
    _configure_runner_id(root, "rn_1")
    store = _store(root)
    store.record_runner_identity(RunnerIdentity(runner_id="rn_1", runner_name="r-one", registered_at=_NOW))
    store.record_served_projects(
        (ServedProject("blizzard", "prj_1", "blizzard", "Blizzard"), ServedProject("old-slug")), at=_NOW
    )
    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert '  serves: blizzard; unresolved: "old-slug"; not yet answered: "blizzard-context"' in result.output


@pytest.mark.component
def test_status_names_two_bindings_resolving_to_one_project_as_not_claiming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_runner(tmp_path)
    _edit_host_file(
        root,
        'projects = [{ project = "default", workspace = "workspace-local", agents = 1 }]',
        'projects = [{ project = "blizzard", workspace = "workspace-local", agents = 1 }, '
        '{ project = "old-slug", workspace = "workspace-local", agents = 1 }]',
    )
    _no_hub(monkeypatch)
    _configure_runner_id(root, "rn_1")
    store = _store(root)
    store.record_runner_identity(RunnerIdentity(runner_id="rn_1", runner_name="r-one", registered_at=_NOW))
    store.record_served_projects(
        (
            ServedProject("blizzard", "prj_1", "blizzard", "Blizzard"),
            ServedProject("old-slug", "prj_1", "blizzard", "Blizzard"),
        ),
        at=_NOW,
    )
    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert (
        "  NOT CLAIMING: blizzard, old-slug all resolve to project prj_1 — bind each project once"
        in result.output.splitlines()
    )


@pytest.mark.component
def test_status_marks_a_fresh_runners_default_not_yet_answered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert '  serves: not yet answered: "default"' in result.output


@pytest.mark.component
def test_status_names_both_runners_and_the_reset_command_while_refusing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    with _serve_local_api(root, refusing=("rn_store", "rn_other")):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    refusing = next(line for line in result.output.splitlines() if "REFUSING" in line)
    assert "token belongs to runner rn_other" in refusing
    assert "not the configured rn_store" in refusing
    assert "BZ_HUB_TOKEN_RUNNER_LOCAL" in refusing and "`[[runner]] id`" in refusing
    assert "store reset" not in refusing


@pytest.mark.component
def test_status_names_the_windows_refusal_while_the_version_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    with _serve_local_api(root, version_refused="upgrade the runner to 0.9 or later"):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert "  REFUSING: upgrade the runner to 0.9 or later" in result.output.splitlines()


@pytest.mark.component
def test_status_renders_the_configured_gates_with_the_hub_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_runner(tmp_path)
    _edit_host_file(root, "gates = []", 'gates = ["build", "review"]')
    _no_hub(monkeypatch)
    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert "gates: build, review" in result.output


@pytest.mark.component
def test_status_renders_a_miss_reason_for_a_lapsed_subscription(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    store = _store(root)
    store.record_external_usage_attempt(
        slug="anthropic",
        sampled_at=_NOW,
        payload=None,
        report_kind="",
        report_payload="",
        miss_reason="credential_lapsed",
    )

    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert "anthropic (anthropic): miss (credential lapsed: log in again)" in result.output


@pytest.mark.component
@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (RenewalOutcome(RenewalOutcomeKind.RENEWED), "renewal: renewed at 2026-07-16T12:00:00+00:00"),
        (
            RenewalOutcome(RenewalOutcomeKind.FAILED, RenewalFailureReason.TIMED_OUT),
            "renewal: failed (timed out) at 2026-07-16T12:00:00+00:00",
        ),
        (None, "renewal: attempted at 2026-07-16T12:00:00+00:00, outcome not recorded"),
    ],
    ids=["renewed", "failed", "unrecorded"],
)
def test_status_renders_the_newest_renewal_typed_beside_a_successful_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: RenewalOutcome | None, expected: str
) -> None:
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    store = _store(root)
    store.record_external_usage_attempt(
        slug="anthropic",
        sampled_at=_NOW,
        payload="{}",
        report_kind="external_subscription_usage.sampled",
        report_payload="{}",
    )
    claim_id = store.claim_credential_renewal(slug="anthropic", claimed_at=_NOW)
    if outcome is not None:
        store.record_credential_renewal_outcome(claim_id=claim_id, outcome=outcome, recorded_at=_NOW)

    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert "anthropic (anthropic): ok, sampled at" in result.output
    assert expected in result.output


@pytest.mark.component
def test_status_renders_no_renewal_line_for_a_slug_never_renewed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    _store(root)

    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert "renewal:" not in result.output


@pytest.mark.component
def test_status_omits_an_unheld_pool_slot_from_held_environments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GET /api/environments`` carries the whole configured pool; an
    unheld slot must not leak into the CLI's held-environments section."""
    root = _init_runner(tmp_path)
    _edit_host_file(root, 'envs = ["e1"]', 'envs = ["e1", "e2"]')
    _no_hub(monkeypatch)
    store = _registered_store(root)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)

    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert "held environments (1):" in result.output
    assert "e1" in result.output
    assert "e2" not in result.output


@pytest.mark.component
def test_status_renders_every_other_section_when_the_trace_status_read_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    with _serve_local_api(root, trace_status=False):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert "subscriptions (1):" in result.output
    assert "harness telemetry" not in result.output


@pytest.mark.unit
def test_status_reports_a_daemon_that_is_not_running(tmp_path: Path) -> None:
    root = _init_runner(tmp_path)  # initialized, but nothing is serving
    result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code != 0
    assert "no runner daemon is serving" in result.output


@pytest.mark.component
def test_status_prints_the_local_pause_reason_on_the_brake_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    _registered_store(root).record_local_pause(
        paused=True,
        at=_NOW,
        by="usage-limit",
        report_kind="runner.locally_paused",
        report_payload="{}",
        reason="usage limit: claude_code",
    )
    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert "paused [local] — usage limit: claude_code" in result.output


def _seed_escalation(store: SqlAlchemyRunnerStore, *, reason: str) -> None:
    store.record_lease(
        NewLease(
            lease_id="lease_9",
            chunk_id="ch_9",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            retries_max=0,
            created_at=_NOW,
        )
    )
    store.record_closure(lease_id="lease_9", chunk_id="ch_9", node_id="nd_build", reason=reason, closed_at=_NOW)


def _record_daemon_paths(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    paths: list[str] = []
    original = RunnerDaemon.get

    def recording(self: RunnerDaemon, path: str, *, params: dict[str, str] | None = None) -> httpx.Response:
        paths.append(path)
        return original(self, path, params=params)

    monkeypatch.setattr(RunnerDaemon, "get", recording)
    return paths


@pytest.mark.component
def test_status_prints_harness_health_under_a_no_acceptable_harness_escalation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    _seed_escalation(_registered_store(root), reason="no-acceptable-harness-mint")
    paths = _record_daemon_paths(monkeypatch)

    with _serve_local_api(root) as (_, tcp_url):
        (runner,) = httpx.get(f"{tcp_url}/api/host/runners").json()["items"]
        health = httpx.get(f"{tcp_url}/runners/{runner['runner_id']}/api/harness-health").json()["items"]
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert "/api/harness-health" in paths
    lines = result.output.splitlines()
    block = lines[lines.index("    cause: no-acceptable-harness") :]
    assert health
    for item in health:
        availability = "available" if item["available"] else "unavailable"
        assert f"    harness {item['harness_id']}: {availability}, cause={item['cause'] or 'none'}" in block


@pytest.mark.component
def test_status_reads_no_harness_health_without_a_no_acceptable_harness_escalation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    _seed_escalation(_registered_store(root), reason="escalated")
    paths = _record_daemon_paths(monkeypatch)

    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert "escalations (1):" in result.output
    assert "/api/harness-health" not in paths
    assert "    harness " not in result.output
    assert "cause:" not in result.output


@pytest.mark.component
def test_status_names_a_held_binding_matching_no_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    store = _registered_store(root)
    store.record_binding(chunk_id="ch_9", environment_id="e9", workdir="/elsewhere/e9", bound_at=_NOW)
    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert "held bindings matching no workspace (1):" in result.output
    assert "e9  chunk=ch_9  workdir=/elsewhere/e9" in result.output


@contextmanager
def _serve_host_of_halted_runners(root: Path) -> Iterator[Path]:
    """The real host composition over ``root``: each runner its file declares is built or left halted, and the app
    is the one a host with none built serves."""
    config = load_host_config(root, port=0)
    projections = config.projections()
    halted = [p for p in projections if isinstance(p, HaltedRunner)]
    built = [p for p in projections if isinstance(p, BuiltRunner)]
    host = build_host_process(config, built, halted=halted)
    hosted = build_hosted_app(config.host_view(), host)
    sockets = host.listeners.bound()
    server = uvicorn.Server(uvicorn.Config(hosted.app, log_level="warning"))
    thread = threading.Thread(target=lambda: server.run(sockets=sockets), daemon=True)
    thread.start()
    try:
        _await_socket(config.layout.socket_path)
        yield config.layout.socket_path
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)
        Uds(config.layout.socket_path).unlink()
        hosted.close()
        host.close()


@pytest.mark.component
def test_status_of_a_host_that_built_no_runner_prints_why_each_one_was_left_unbuilt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_runner(tmp_path)
    _edit_host_file(root, 'subscription = "anthropic"', 'subscription = "missing"')
    _no_hub(monkeypatch)
    with _serve_host_of_halted_runners(root):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])
        health = httpx.Client(transport=httpx.HTTPTransport(uds=str(root / "host.sock")), base_url="http://host")
        served = health.get("/api/health").status_code

    assert served == 200  # the host serves while its only runner is halted
    assert result.exit_code != 0
    assert "serves no runner" in result.output
    assert "runner runner-local" in result.output and "names subscription 'missing'" in result.output


@pytest.mark.component
@pytest.mark.parametrize("answered", [404, 500])
def test_status_fails_on_any_runner_read_status_but_the_one_a_runnerless_host_answers(
    answered: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only the 503 of a host that built no runner reads as "serves no runner"; any other refusal surfaces as it is."""
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(answered, json={"detail": "refused"})),
        base_url="http://host",
    )
    monkeypatch.setattr(RunnerDaemon, "reach", classmethod(lambda cls, *_args: RunnerDaemon("status", client, "host")))

    result = CliRunner().invoke(runner_group, ["status", "--dir", str(tmp_path)])

    assert result.exit_code != 0
    assert str(answered) in result.output
    assert "serves no runner" not in result.output


@pytest.mark.component
def test_status_selects_a_host_of_ones_runner_by_name_by_id_or_unnamed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    with _serve_local_api(root) as (_, tcp_url):
        (runner,) = httpx.get(f"{tcp_url}/api/host/runners").json()["items"]
        results = [
            CliRunner().invoke(runner_group, ["status", "--dir", str(root), *selector])
            for selector in ([], ["--runner", runner["name"]], ["--runner", runner["runner_id"]])
        ]

    for result in results:
        assert result.exit_code == 0, result.output
    assert results[1].output == results[0].output == results[2].output


@pytest.mark.component
def test_an_unmatched_runner_fails_naming_the_hosts_runners(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    with _serve_local_api(root) as (_, tcp_url):
        (runner,) = httpx.get(f"{tcp_url}/api/host/runners").json()["items"]
        result = CliRunner().invoke(runner_group, ["pause", "--dir", str(root), "--runner", "nope"])

    assert result.exit_code != 0
    assert "holds no runner 'nope'" in result.output
    assert f"{runner['name']} ({runner['runner_id']}, serving)" in result.output


def _host_of(items: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch, paths: list[str]) -> None:
    """Every verb reaches a mock host whose runner list is ``items`` and which answers any other call as runner ``beta``."""

    def answer(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/host/runners":
            return httpx.Response(200, json={"items": items})
        return httpx.Response(200, json={"runner_name": "beta"})

    def reach(
        cls: type[RunnerDaemon], verb: str, _dir: str, _url: str | None, _trace: object, runner: str | None = None
    ) -> RunnerDaemon:
        client = httpx.Client(transport=httpx.MockTransport(answer), base_url="http://host")
        return RunnerDaemon(verb, client, "host", runner)

    monkeypatch.setattr(RunnerDaemon, "reach", classmethod(reach))


_TWO_RUNNERS: list[dict[str, object]] = [
    {"runner_id": "rn_a", "name": "alpha", "state": "serving", "halted_reasons": []},
    {"runner_id": "rn_b", "name": "beta", "state": "serving", "halted_reasons": []},
]


@pytest.mark.component
def test_an_unnamed_runner_on_a_host_of_several_fails_naming_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths: list[str] = []
    _host_of(_TWO_RUNNERS, monkeypatch, paths)

    result = CliRunner().invoke(runner_group, ["requeue", "ch_1", "--dir", str(tmp_path)])

    assert result.exit_code != 0
    assert "holds several runners" in result.output and "--runner" in result.output
    assert "alpha (rn_a, serving)" in result.output and "beta (rn_b, serving)" in result.output
    assert paths == ["/api/host/runners"]


@pytest.mark.component
@pytest.mark.parametrize("selector", ["beta", "rn_b"])
def test_a_named_runner_on_a_host_of_several_is_the_one_addressed(
    selector: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths: list[str] = []
    _host_of(_TWO_RUNNERS, monkeypatch, paths)

    result = CliRunner().invoke(runner_group, ["start", "--dir", str(tmp_path), "--runner", selector])

    assert result.exit_code == 0, result.output
    assert paths == ["/api/host/runners", "/runners/rn_b/api/runner"]


@pytest.mark.component
def test_a_name_shared_by_two_runners_fails_naming_their_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths: list[str] = []
    twins = [{**item, "name": "alpha"} for item in _TWO_RUNNERS]
    _host_of(twins, monkeypatch, paths)

    result = CliRunner().invoke(runner_group, ["start", "--dir", str(tmp_path), "--runner", "alpha"])

    assert result.exit_code != 0
    assert "matches several runners" in result.output
    assert "rn_a" in result.output and "rn_b" in result.output
    assert paths == ["/api/host/runners"]
