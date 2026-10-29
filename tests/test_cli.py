"""CLI smoke — the verb surface exists and the real verbs work (unit tier).

The scaffold implements ``init`` / ``migrate`` / ``host`` for real; the remaining
verbs are present as self-naming stubs. This exercises the wiring,
not the daemon runtime (``host`` blocks on a server and is not driven here).
"""

from __future__ import annotations

import os
import shutil
import socket
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from click.testing import CliRunner
from sqlalchemy import insert

from blizzard.cli.collaborators import CliCollaborators
from blizzard.cli.main import blizzard
from blizzard.cli.operator_trace import UNTRACED
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.store import MIGRATIONS_DIR as HUB_MIGRATIONS_DIR
from blizzard.runner.cli.daemon import LOCAL_CLIENT_TIMEOUT, Door, daemon_holding
from blizzard.runner.cli.runtime import InitCollaborators
from blizzard.runner.host_layout import HostLayout, HostLock
from blizzard.runner.store import MIGRATIONS_DIR as RUNNER_MIGRATIONS_DIR
from blizzard.runner.store import schema as runner_schema
from tests.host_files import load_runner_config
from tests.runner_fakes import FIXTURE_RUNNER_ID
from tests.runner_init_fakes import init_runner

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(("daemon", "tree"), [("hub", HUB_MIGRATIONS_DIR), ("runner", RUNNER_MIGRATIONS_DIR)])
def test_migrate_revision_queries_need_no_runtime_directory(daemon: str, tree: Path, tmp_path: Path) -> None:
    missing = tmp_path / "absent"
    cli = CliRunner()
    expected = MigrationRunner(tree, "sqlite:////dev/null").script_head()
    assert expected is not None
    parent = ScriptDirectory(str(tree)).get_revision(expected)
    assert parent is not None and isinstance(parent.down_revision, str)
    prefix = [daemon, "migrate", "--dir", str(missing)]

    head = cli.invoke(blizzard, [*prefix, "--head"])
    assert head.exit_code == 0, head.output
    assert head.stdout == f"{expected}\n"
    for revision in (expected, parent.down_revision):
        known = cli.invoke(blizzard, [*prefix, "--knows", revision])
        assert known.exit_code == 0, known.output
        assert known.stdout == ""
    unknown = cli.invoke(blizzard, [*prefix, "--knows", "unknown_revision"])
    assert unknown.exit_code == 1
    assert unknown.stdout == ""
    assert not missing.exists()


@pytest.mark.parametrize("daemon", ["hub", "runner"])
@pytest.mark.parametrize(
    ("first", "second"),
    [("--head", "--knows"), ("--head", "--down"), ("--knows", "--down")],
)
def test_migrate_revision_flags_are_exclusive(daemon: str, first: str, second: str, tmp_path: Path) -> None:
    missing = tmp_path / "absent"
    args = [daemon, "migrate", "--dir", str(missing)]
    for flag in (first, second):
        args.extend([flag] if flag == "--head" else [flag, "base"])
    result = CliRunner().invoke(blizzard, args)
    assert result.exit_code == 2
    assert first in result.output and second in result.output
    assert not missing.exists()


_DOOR = Door(UNTRACED, CliCollaborators.production().client_factory)


def test_root_lists_hub_and_runner() -> None:
    result = init_runner().invoke(blizzard, ["--help"])
    assert result.exit_code == 0
    assert "hub" in result.output
    assert "runner" in result.output


def test_hub_lists_its_verbs() -> None:
    # The operator verbs are grouped by noun.
    result = init_runner().invoke(blizzard, ["hub", "--help"])
    assert result.exit_code == 0
    for verb in ("init", "migrate", "host", "status", "chunk", "runner", "graph", "queue", "decision", "question"):
        assert verb in result.output


def test_hub_removed_flat_verbs_are_unknown() -> None:
    """Flat verbs removed no longer name a command in `--help` (matched by
    each line's own first token, not a substring), and invoking one fails with click's
    unknown-command error rather than silently delegating."""
    result = init_runner().invoke(blizzard, ["hub", "--help"])
    assert result.exit_code == 0
    listed = {line.split()[0] for line in result.output.splitlines() if line.startswith("  ") and line.split()}
    for verb in ("answer", "ingest", "promote", "requeue", "decisions", "decide", "pause-chunk", "resume-chunk"):
        assert verb not in listed

    invoked = init_runner().invoke(blizzard, ["hub", "promote", "ch_42"])
    assert invoked.exit_code != 0
    assert "No such command 'promote'" in invoked.output


def test_runner_lists_its_verbs() -> None:
    result = init_runner().invoke(blizzard, ["runner", "--help"])
    assert result.exit_code == 0
    for verb in ("init", "migrate", "host", "heartbeat", "ask", "takeover", "requeue", "transcript"):
        assert verb in result.output


def test_hub_init_and_migrate(tmp_path: Path) -> None:
    runner = init_runner()
    root = str(tmp_path / "hub")

    init_result = runner.invoke(blizzard, ["hub", "init", root])
    assert init_result.exit_code == 0, init_result.output
    assert (tmp_path / "hub" / "blizzard-hub.toml").exists()

    migrate_result = runner.invoke(blizzard, ["hub", "migrate", "--dir", root])
    assert migrate_result.exit_code == 0, migrate_result.output


def test_hub_migrate_rejects_a_leftover_pm_source_block(tmp_path: Path) -> None:
    """`migrate` — not just `host` — must reject the pre-rename key: the
    dogfooding deploy runs `migrate` before `systemctl restart`, so a `host`-only guard
    would pass migrate, take the hub down at restart, and the runner with it."""
    root = tmp_path / "hub"
    runner = init_runner()
    assert runner.invoke(blizzard, ["hub", "init", str(root)]).exit_code == 0

    config_path = root / "blizzard-hub.toml"
    config_path.write_text(
        config_path.read_text()
        + '\n[[pm_source]]\nname = "blizzard"\nprovider = "github"\nrepo = "o/r"\ntoken_env = "T"\n'
    )

    result = runner.invoke(blizzard, ["hub", "migrate", "--dir", str(root)])

    assert result.exit_code != 0, f"migrate accepted a stale [[pm_source]] config:\n{result.output}"
    assert "[[work_source]]" in result.output, f"the error must name the new key: {result.output}"


def test_hub_migrate_refuses_a_db_url_copied_from_elsewhere(tmp_path: Path) -> None:
    """`cp -r <live-store>/* <copy>/ && blizzard hub migrate --dir <copy>`
    must refuse rather than silently migrate the live store — an absolute db_url is
    written in directly to model an unpatched-era init or an explicit override."""
    runner = init_runner()
    live = tmp_path / "live"
    assert runner.invoke(blizzard, ["hub", "init", str(live)]).exit_code == 0
    live_db_url = f"sqlite:///{(live / 'data' / 'hub.db').resolve()}"

    copy_dir = tmp_path / "copy"
    shutil.copytree(live, copy_dir)
    config_path = copy_dir / "blizzard-hub.toml"
    config_path.write_text(f'db_url = "{live_db_url}"\n' + config_path.read_text())

    refused = runner.invoke(blizzard, ["hub", "migrate", "--dir", str(copy_dir)])
    assert refused.exit_code != 0, f"migrate silently touched a db_url outside --dir:\n{refused.output}"
    assert str(copy_dir) in refused.output
    assert live_db_url.removeprefix("sqlite:///") in refused.output

    allowed = runner.invoke(blizzard, ["hub", "migrate", "--dir", str(copy_dir), "--allow-external-db"])
    assert allowed.exit_code == 0, allowed.output


def test_hub_init_produces_a_config_a_copy_can_migrate_from_with_no_flags(tmp_path: Path) -> None:
    """Acceptance: a freshly-inited dir can be `cp -r`'d and driven from the copy with
    no flags — `hub init`'s output embeds no absolute default path."""
    runner = init_runner()
    original = tmp_path / "original"
    assert runner.invoke(blizzard, ["hub", "init", str(original)]).exit_code == 0

    copy_dir = tmp_path / "copy"
    shutil.copytree(original, copy_dir)

    result = runner.invoke(blizzard, ["hub", "migrate", "--dir", str(copy_dir)])
    assert result.exit_code == 0, result.output


def test_hub_init_refuses_a_db_url_copied_from_elsewhere(tmp_path: Path) -> None:
    """`init` is idempotent and re-running it on an already-inited directory takes the
    same ``HubConfig.load`` path `migrate`/`host` do — it must refuse the same way rather
    than crash with a raw traceback, and accept the same escape hatch."""
    runner = init_runner()
    live = tmp_path / "live"
    assert runner.invoke(blizzard, ["hub", "init", str(live)]).exit_code == 0
    live_db_url = f"sqlite:///{(live / 'data' / 'hub.db').resolve()}"

    copy_dir = tmp_path / "copy"
    shutil.copytree(live, copy_dir)
    config_path = copy_dir / "blizzard-hub.toml"
    config_path.write_text(f'db_url = "{live_db_url}"\n' + config_path.read_text())

    refused = runner.invoke(blizzard, ["hub", "init", str(copy_dir)])
    assert refused.exit_code != 0, f"init silently touched a db_url outside its directory:\n{refused.output}"
    assert "Traceback" not in refused.output, f"init must raise a clean ClickException, not crash:\n{refused.output}"
    assert str(copy_dir) in refused.output
    assert live_db_url.removeprefix("sqlite:///") in refused.output

    allowed = runner.invoke(blizzard, ["hub", "init", str(copy_dir), "--allow-external-db"])
    assert allowed.exit_code == 0, allowed.output


def test_dev_check_invariants_refuses_a_hub_db_url_copied_from_elsewhere(tmp_path: Path) -> None:
    """`blizzard dev check-invariants --hub-dir` loads a `HubConfig` the same way `migrate`/
    `host`/`init` do, and must refuse a copied-in external db_url the same clean way rather
    than crash — it accepts the same escape hatch too."""
    runner = init_runner()
    live = tmp_path / "live"
    assert runner.invoke(blizzard, ["hub", "init", str(live)]).exit_code == 0
    live_db_url = f"sqlite:///{(live / 'data' / 'hub.db').resolve()}"

    copy_dir = tmp_path / "copy"
    shutil.copytree(live, copy_dir)
    config_path = copy_dir / "blizzard-hub.toml"
    config_path.write_text(f'db_url = "{live_db_url}"\n' + config_path.read_text())

    refused = runner.invoke(blizzard, ["dev", "check-invariants", "--hub-dir", str(copy_dir)])
    assert refused.exit_code != 0, (
        f"check-invariants silently touched a db_url outside its directory:\n{refused.output}"
    )
    assert "Traceback" not in refused.output, f"must raise a clean ClickException, not crash:\n{refused.output}"
    assert str(copy_dir) in refused.output

    allowed = runner.invoke(blizzard, ["dev", "check-invariants", "--hub-dir", str(copy_dir), "--allow-external-db"])
    assert allowed.exit_code == 0, allowed.output


def test_dev_check_invariants_asks_for_active_lease_process_is_live(tmp_path: Path) -> None:
    """A live operator is never mid-crash-recovery-window — the check must run with
    ``after_recovery=True``, or a genuinely leaked provisional generation goes unreported."""
    root = tmp_path / "runner"
    runner = init_runner()
    assert runner.invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0

    engine = create_engine_from_url(load_runner_config(root).db_url)
    with engine.begin() as conn:
        conn.execute(
            insert(runner_schema.leases).values(
                runner_id=FIXTURE_RUNNER_ID,
                lease_id="lease_a",
                chunk_id="ch_1",
                epoch=1,
                created_at=datetime(2026, 7, 14, tzinfo=UTC),
                pid=999999,  # not a real, live pid
                process_start_time="start-999999",
            )
        )

    result = runner.invoke(blizzard, ["dev", "check-invariants", "--runner-dir", str(root)])

    assert result.exit_code != 0, result.output
    assert "active-lease-process-is-live" in result.output


def test_runner_init(tmp_path: Path) -> None:
    root = str(tmp_path / "runner")
    result = init_runner().invoke(blizzard, ["runner", "init", root])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "runner" / "blizzard-host.toml").exists()


# The runtime-dir env fallback: --dir > $BZ_<daemon>_DIR > cwd. Parametrized
# over both daemons — a fallback wired on one but not the other is the drift worth catching.
_DAEMONS = [("hub", "BZ_HUB_DIR", "blizzard-hub.toml"), ("runner", "BZ_RUNNER_DIR", "blizzard-host.toml")]


@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_dir_resolves_from_env_when_flag_absent(daemon: str, env_var: str, config_name: str, tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    cli = init_runner()
    assert cli.invoke(blizzard, [daemon, "init", str(root)], env={env_var: None}).exit_code == 0

    # No --dir: the env names the runtime root, and `migrate` finds the store there.
    result = cli.invoke(blizzard, [daemon, "migrate"], env={env_var: str(root)})
    assert result.exit_code == 0, result.output
    assert "migrated" in result.output


@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_dir_flag_beats_env(daemon: str, env_var: str, config_name: str, tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    cli = init_runner()
    assert cli.invoke(blizzard, [daemon, "init", str(root)], env={env_var: None}).exit_code == 0

    # The env names a dir that was never initialized, so this only succeeds if --dir wins.
    result = cli.invoke(blizzard, [daemon, "migrate", "--dir", str(root)], env={env_var: str(tmp_path / "unused")})
    assert result.exit_code == 0, result.output
    assert "migrated" in result.output


@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_dir_defaults_to_cwd_when_neither_set(
    daemon: str, env_var: str, config_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = init_runner()
    assert cli.invoke(blizzard, [daemon, "init", str(tmp_path)], env={env_var: None}).exit_code == 0
    monkeypatch.chdir(tmp_path)

    # Neither rung set: unchanged behavior — `.` is the runtime root.
    result = cli.invoke(blizzard, [daemon, "migrate"], env={env_var: None})
    assert result.exit_code == 0, result.output
    assert "migrated" in result.output


@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_init_directory_argument_resolves_from_env(daemon: str, env_var: str, config_name: str, tmp_path: Path) -> None:
    # `init`'s positional DIRECTORY honors the same variable, so a band-aimed env
    # scaffolds the root it names rather than the cwd.
    root = tmp_path / "runtime"
    result = init_runner().invoke(blizzard, [daemon, "init"], env={env_var: str(root)})
    assert result.exit_code == 0, result.output
    assert (root / config_name).exists()


@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_dir_help_names_the_env_fallback(daemon: str, env_var: str, config_name: str) -> None:
    result = init_runner().invoke(blizzard, [daemon, "migrate", "--help"])
    assert result.exit_code == 0
    assert f"${env_var}" in result.output


# `host` accepting a positional DIRECTORY like `init` does: the config-load
# guard fails fast, before serving, naming the resolved directory — proof with nothing started.
@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_host_accepts_positional_directory(daemon: str, env_var: str, config_name: str, tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    result = init_runner().invoke(blizzard, [daemon, "host", str(root)], env={env_var: None})
    assert result.exit_code != 0
    assert str(root) in result.output
    assert "serving blizzard" not in result.output


@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_host_dir_option_still_works(daemon: str, env_var: str, config_name: str, tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    result = init_runner().invoke(blizzard, [daemon, "host", "--dir", str(root)], env={env_var: None})
    assert result.exit_code != 0
    assert str(root) in result.output
    assert "serving blizzard" not in result.output


@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_host_positional_and_dir_option_agreeing(daemon: str, env_var: str, config_name: str, tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    result = init_runner().invoke(blizzard, [daemon, "host", str(root), "--dir", str(root)], env={env_var: None})
    assert result.exit_code != 0
    assert str(root) in result.output
    assert "disagree" not in result.output


@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_host_positional_and_dir_option_conflict(daemon: str, env_var: str, config_name: str, tmp_path: Path) -> None:
    positional = tmp_path / "positional"
    flagged = tmp_path / "flagged"
    result = init_runner().invoke(
        blizzard, [daemon, "host", str(positional), "--dir", str(flagged)], env={env_var: None}
    )
    assert result.exit_code != 0
    assert str(positional) in result.output
    assert str(flagged) in result.output
    assert "disagree" in result.output


@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_host_positional_beats_ambient_env_dir(daemon: str, env_var: str, config_name: str, tmp_path: Path) -> None:
    # A bare ambient $BZ_<daemon>_DIR doesn't trigger the conflict check (only an
    # explicit --dir does), so a disagreeing positional wins outright, silently.
    positional = tmp_path / "positional"
    ambient = tmp_path / "ambient"
    result = init_runner().invoke(blizzard, [daemon, "host", str(positional)], env={env_var: str(ambient)})
    assert result.exit_code != 0
    assert str(positional) in result.output
    assert "disagree" not in result.output


def test_hub_host_help_shows_directory_argument() -> None:
    result = init_runner().invoke(blizzard, ["hub", "host", "--help"])
    assert result.exit_code == 0
    assert "DIRECTORY" in result.output


def test_runner_status_errors_cleanly_with_no_daemon_serving(tmp_path: Path) -> None:
    # `status` is a pure client of the local API, same as `pause`/`start` —
    # no socket, no store fallback, a clean error naming the missing daemon.
    root = tmp_path / "runner"
    assert init_runner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0

    result = init_runner().invoke(blizzard, ["runner", "status", "--dir", str(root)])

    assert result.exit_code != 0
    assert "no runner daemon is serving" in result.output


def _serve_on(sock_path: Path, reply: bytes | None) -> socket.socket:
    """A minimal UDS listener the liveness probe can reach. ``reply is None`` accepts the
    connection and never answers — the wedged-daemon case."""
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)

    def _answer() -> None:
        conn, _ = server.accept()
        with conn:
            conn.recv(4096)
            if reply is not None:
                conn.sendall(reply)
            else:
                time.sleep(LOCAL_CLIENT_TIMEOUT * 3)

    threading.Thread(target=_answer, daemon=True).start()
    return server


_OK = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}"
_UNAVAILABLE = b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 2\r\n\r\n{}"


def test_a_stale_socket_file_is_not_a_serving_daemon(tmp_path: Path) -> None:
    """The single-writer guard probes rather than stats: a socket an ungraceful exit left
    behind must not refuse a verb nothing is actually holding."""
    root = tmp_path / "runner"
    assert init_runner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0
    HostLayout(root).legacy_socket_path.write_bytes(b"")

    assert daemon_holding(HostLayout(root).legacy_socket_path, _DOOR) is None


def test_a_daemon_answering_its_socket_is_holding_the_store(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    assert init_runner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0
    server = _serve_on(HostLayout(root).legacy_socket_path, _OK)

    try:
        assert daemon_holding(HostLayout(root).legacy_socket_path, _DOOR) is not None
    finally:
        server.close()


@pytest.mark.parametrize(("reply", "expected"), [(None, "did not answer"), (_UNAVAILABLE, "503")])
def test_an_ambiguous_liveness_answer_fails_closed(reply: bytes | None, expected: str, tmp_path: Path) -> None:
    """A wedged or unhealthy daemon still HOLDS the single-writer store. Resolving either
    ambiguity toward "nothing there" would let the verb write concurrently with it."""
    root = tmp_path / "runner"
    assert init_runner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0
    server = _serve_on(HostLayout(root).legacy_socket_path, reply)

    try:
        holding = daemon_holding(HostLayout(root).legacy_socket_path, _DOOR)
    finally:
        server.close()

    assert holding is not None and expected in holding


def test_runner_transcript_backfill_refuses_while_the_lane_is_off(tmp_path: Path) -> None:
    # `[transcripts] ship` is false in a scaffolded config, and a backfill
    # into a lane the operator has switched off would ship content they never enabled.
    root = tmp_path / "runner"
    assert init_runner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0

    result = init_runner().invoke(blizzard, ["runner", "transcript", "backfill", "--dir", str(root)])

    assert result.exit_code != 0
    assert "[transcripts] ship is false" in result.output


def _enable_transcript_shipping(root: Path) -> None:
    config_path = root / "blizzard-host.toml"
    config_path.write_text(config_path.read_text().replace("ship = false", "ship = true", 1))


def test_runner_transcript_backfill_drives_its_whole_production_route(tmp_path: Path) -> None:
    """`bzh:gating-tier-pins-production-paths`: the component tier constructs the service
    directly, so without this the composition-root wiring the operator actually runs
    (CLI -> LoopWiring.backfill_transcripts -> the report line) is named by no gating test."""
    root = tmp_path / "runner"
    assert init_runner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0
    _enable_transcript_shipping(root)

    result = init_runner().invoke(blizzard, ["runner", "transcript", "backfill", "--dir", str(root), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "would import 0, already present 0, gone 0" in result.output


def test_runner_transcript_reship_drives_its_whole_production_route(tmp_path: Path) -> None:
    """`bzh:gating-tier-pins-production-paths`, for the second verb: the component tier
    constructs the service directly, so the CLI -> `LoopWiring.reship_transcript` ->
    `TranscriptReshipError` -> `ClickException` route is otherwise named by no gating test."""
    root = tmp_path / "runner"
    assert init_runner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0
    _enable_transcript_shipping(root)

    result = init_runner().invoke(blizzard, ["runner", "transcript", "reship", "seg_nope", "--dir", str(root)])

    assert result.exit_code != 0
    assert "no such transcript segment: seg_nope" in result.output


@pytest.mark.parametrize("verb", [["reship", "seg_x"], ["backfill", "--dry-run"]])
def test_a_transcript_verb_refuses_a_store_that_names_another_runner_and_changes_nothing(
    tmp_path: Path, verb: list[str]
) -> None:
    root = tmp_path / "runner"
    assert init_runner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0
    _enable_transcript_shipping(root)
    config_path = root / "blizzard-host.toml"
    config_path.write_text(config_path.read_text().replace('id = "rn_init1"', 'id = "rn_other"'))

    result = init_runner().invoke(blizzard, ["runner", "transcript", *verb, "--dir", str(root)])

    assert result.exit_code == 1
    assert "the store belongs to runner rn_init1, not the configured rn_other" in result.output
    assert f"`blizzard runner store reset {root}`" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("verb", [["reship", "seg_x"], ["backfill", "--dry-run"]])
def test_a_transcript_verb_refuses_a_runner_that_has_not_joined_its_hub(tmp_path: Path, verb: list[str]) -> None:
    root = tmp_path / "runner"
    assert init_runner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0
    _enable_transcript_shipping(root)
    config_path = root / "blizzard-host.toml"
    config_path.write_text(config_path.read_text().replace('id = "rn_init1"\n', "", 1))

    result = init_runner().invoke(blizzard, ["runner", "transcript", *verb, "--dir", str(root)])

    assert result.exit_code == 1
    assert "has not joined its hub" in result.output
    assert isinstance(result.exception, SystemExit)


def test_runner_transcript_reship_refuses_while_a_daemon_holds_the_store(tmp_path: Path) -> None:
    """The shared `_transcript_config` guard reaches the second verb too — the whole reason
    it was extracted, and a regression here is silent store corruption, not a failed command."""
    root = tmp_path / "runner"
    assert init_runner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0
    _enable_transcript_shipping(root)
    server = _serve_on(HostLayout(root).legacy_socket_path, _OK)

    try:
        result = init_runner().invoke(blizzard, ["runner", "transcript", "reship", "seg_x", "--dir", str(root)])
    finally:
        server.close()

    assert result.exit_code != 0
    assert "so this cannot" in result.output


def test_runner_transcript_backfill_refuses_while_a_daemon_holds_the_store(tmp_path: Path) -> None:
    """The single-writer guard, exercised through the verb rather than the helper alone."""
    root = tmp_path / "runner"
    assert init_runner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0
    _enable_transcript_shipping(root)
    server = _serve_on(HostLayout(root).legacy_socket_path, _OK)

    try:
        result = init_runner().invoke(blizzard, ["runner", "transcript", "backfill", "--dir", str(root)])
    finally:
        server.close()

    assert result.exit_code != 0
    assert "so this cannot" in result.output


_STORE_WRITERS = {
    "reship": (["transcript", "reship", "seg_x"], "reship a transcript"),
    "tick": (["tick"], "run a tick"),
    "host": (["host"], "start the host"),
}


@pytest.mark.parametrize("verb", _STORE_WRITERS)
@pytest.mark.parametrize("held_by", ["lock", "socket"])
def test_every_store_writing_verb_refuses_a_held_directory_in_one_wording(
    verb: str, held_by: str, tmp_path: Path
) -> None:
    """One guard fronts `transcript`, `tick` and `host`: it names the holder and the action, and leaves the store."""
    root = tmp_path / "runner"
    assert init_runner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0
    _enable_transcript_shipping(root)
    layout = HostLayout(root)
    store_before = layout.store_path.read_bytes() if layout.store_path.exists() else None
    args, action = _STORE_WRITERS[verb]
    server = _serve_on(layout.legacy_socket_path, _OK) if held_by == "socket" else None
    lock = HostLock.acquire(layout) if held_by == "lock" else None

    try:
        result = init_runner().invoke(blizzard, ["runner", *args, "--dir", str(root)])
    finally:
        if server is not None:
            server.close()
        if lock is not None:
            lock.release()

    assert result.exit_code != 0
    cause = f"pid {os.getpid()}" if held_by == "lock" else str(layout.legacy_socket_path)
    assert f"{cause}" in result.output
    assert f"so this cannot {action} — stop it first" in result.output
    assert (layout.store_path.read_bytes() if layout.store_path.exists() else None) == store_before


def test_runner_takeover_errors_cleanly_with_no_daemon_serving(tmp_path: Path) -> None:
    # `takeover` is a pure client of the local API too — no socket, no store
    # fallback, a clean error naming the missing daemon.
    root = tmp_path / "runner"
    assert init_runner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0

    result = init_runner().invoke(blizzard, ["runner", "takeover", "ch_1", "--dir", str(root)])

    assert result.exit_code != 0
    assert "no runner daemon is serving" in result.output


def test_hub_host_refuses_a_db_url_copied_from_elsewhere(tmp_path: Path) -> None:
    """`host` applies the same --dir isolation guard as `migrate` — it
    fails before ever announcing "serving", let alone binding a socket."""
    runner = init_runner()
    live = tmp_path / "live"
    assert runner.invoke(blizzard, ["hub", "init", str(live)]).exit_code == 0
    live_db_url = f"sqlite:///{(live / 'data' / 'hub.db').resolve()}"

    copy_dir = tmp_path / "copy"
    shutil.copytree(live, copy_dir)
    config_path = copy_dir / "blizzard-hub.toml"
    config_path.write_text(f'db_url = "{live_db_url}"\n' + config_path.read_text())

    result = runner.invoke(blizzard, ["hub", "host", "--dir", str(copy_dir)])

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert str(copy_dir) in result.output
    assert "serving blizzard-hub" not in result.output


def test_runner_host_without_a_token_names_both_ways_to_get_one(tmp_path: Path) -> None:
    """A runner the hub already lists takes an enroll, not a second add under a fresh id, so the
    no-token warning names both."""
    runner = init_runner()
    root = tmp_path / "runner"
    assert runner.invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0
    (root / ".env").unlink()

    def stop(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("stop before serving")

    result = runner.invoke(
        blizzard,
        ["runner", "host", "--dir", str(root)],
        obj=replace(InitCollaborators(runner.hub.clients, runner.hub.host_clients), build_host_process=stop),
    )

    assert isinstance(result.exception, RuntimeError)
    assert "runner runner-local" in result.output and "is halted: has no hub token" in result.output
    assert "`blizzard runner init` adds the runner" in result.output
    assert "`blizzard hub runner enroll <id>` prints the line to put there" in result.output


def test_runner_host_reports_a_missing_runner_prompt_file_as_a_clean_error(tmp_path: Path) -> None:
    """A configured-but-missing ``runner_prompt_file`` fails at boot as a
    clean CLI error: ``PeriodicDriver`` resolves it before any socket binds, rather than
    silently killing the reconciliation loop while uvicorn keeps serving."""
    runner = init_runner()
    root = tmp_path / "runner"
    assert runner.invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0
    config_path = root / "blizzard-host.toml"
    config_path.write_text(
        config_path.read_text().replace('runner_prompt_file = ""', 'runner_prompt_file = "does-not-exist.md"')
    )

    result = runner.invoke(blizzard, ["runner", "host", "--dir", str(root)])

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "does-not-exist.md" in result.output
    # It never claims to be serving a daemon it then fails to build.
    assert "serving blizzard-runner" not in result.output


def _host_answering(sock_path: Path, *replies: bytes) -> socket.socket:
    """A UDS listener answering each connection with the next of ``replies``, closing each after one exchange."""
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(len(replies))

    def _answer() -> None:
        for reply in replies:
            conn, _ = server.accept()
            with conn:
                conn.recv(4096)
                conn.sendall(reply)

    threading.Thread(target=_answer, daemon=True).start()
    return server


def _json_reply(status: str, body: str) -> bytes:
    return f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n{body}".encode()


_RUNNERS = _json_reply(
    "200 OK",
    '{"items":[{"name":"r","runner_id":"rn_1","state":"serving","served":true,"halted_reasons":[]}]}',
)


def _tick_on_running_host(tmp_path: Path, args: list[str], *replies: bytes) -> tuple[int, str]:
    root = tmp_path / "runner"
    assert init_runner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0
    layout = HostLayout(root)
    server = _host_answering(layout.socket_path, *replies)
    lock = HostLock.acquire(layout)
    try:
        result = init_runner().invoke(blizzard, ["runner", "tick", "--dir", str(root), *args])
    finally:
        server.close()
        lock.release()
    return result.exit_code, result.output


_HEALTH = _json_reply("200 OK", "{}")
_COMPLETED = _json_reply("200 OK", '{"started_at":"a","finished_at":"b","outcome":"completed","error":null}')
_FAILED = _json_reply("200 OK", '{"started_at":"a","finished_at":"b","outcome":"failed","error":"hub unreachable"}')
_BUSY = _json_reply("409 Conflict", '{"code":"tick_in_progress"}')


def test_tick_steps_a_running_host_through_its_socket(tmp_path: Path) -> None:
    code, output = _tick_on_running_host(tmp_path, [], _HEALTH, _RUNNERS, _COMPLETED)

    assert code == 0
    assert "tick complete" in output


def test_tick_host_steps_the_host_wide_pass(tmp_path: Path) -> None:
    code, output = _tick_on_running_host(tmp_path, ["--host"], _HEALTH, _COMPLETED)

    assert code == 0
    assert "tick complete" in output


@pytest.mark.parametrize(("reply", "said"), [(_FAILED, "hub unreachable"), (_BUSY, "already in progress")])
def test_tick_on_a_running_host_exits_1_unless_the_pass_completed(tmp_path: Path, reply: bytes, said: str) -> None:
    code, output = _tick_on_running_host(tmp_path, [], _HEALTH, _RUNNERS, reply)

    assert code == 1
    assert said in output
    assert "tick complete" not in output


def test_tick_refuses_host_and_runner_together_without_running_work(tmp_path: Path) -> None:
    code, output = _tick_on_running_host(tmp_path, ["--host", "--runner", "r"])

    assert code == 2
    assert "mutually exclusive" in output


def test_tick_host_is_refused_with_no_host_running(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    assert init_runner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0

    result = init_runner().invoke(blizzard, ["runner", "tick", "--host", "--dir", str(root)])

    assert result.exit_code == 1
    assert "no host is serving" in result.output


def test_tick_with_the_lock_held_and_no_answering_socket_still_refuses(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    assert init_runner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0
    layout = HostLayout(root)
    lock = HostLock.acquire(layout)
    try:
        result = init_runner().invoke(blizzard, ["runner", "tick", "--dir", str(root)])
    finally:
        lock.release()

    assert result.exit_code == 1
    assert "so this cannot run a tick — stop it first" in result.output


def test_tick_with_the_lock_held_and_a_dead_socket_file_still_refuses(tmp_path: Path) -> None:
    """A socket file an ungraceful exit left behind answers nothing, so it is not a host to step."""
    root = tmp_path / "runner"
    assert init_runner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0
    layout = HostLayout(root)
    corpse = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    corpse.bind(str(layout.socket_path))
    corpse.close()
    assert layout.socket_path.exists()
    lock = HostLock.acquire(layout)
    try:
        result = init_runner().invoke(blizzard, ["runner", "tick", "--dir", str(root)])
    finally:
        lock.release()

    assert result.exit_code == 1
    assert "so this cannot run a tick — stop it first" in result.output
