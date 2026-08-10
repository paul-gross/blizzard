"""CLI smoke — the verb surface exists and the real verbs work (unit tier).

The scaffold implements ``init`` / ``migrate`` / ``host`` for real; the remaining
verbs are present as self-naming stubs. This exercises the wiring,
not the daemon runtime (``host`` blocks on a server and is not driven here).
"""

from __future__ import annotations

import shutil
import socket
import threading
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.cli.main import blizzard
from blizzard.runner.cli import _daemon_holding
from blizzard.runner.cli_daemon import LOCAL_CLIENT_TIMEOUT
from blizzard.runner.config import RunnerConfig

pytestmark = pytest.mark.unit


def test_root_lists_hub_and_runner() -> None:
    result = CliRunner().invoke(blizzard, ["--help"])
    assert result.exit_code == 0
    assert "hub" in result.output
    assert "runner" in result.output


def test_hub_lists_its_verbs() -> None:
    # The operator verbs are grouped by noun (issue #104).
    result = CliRunner().invoke(blizzard, ["hub", "--help"])
    assert result.exit_code == 0
    for verb in ("init", "migrate", "host", "status", "chunk", "runner", "graph", "queue", "decision", "question"):
        assert verb in result.output


def test_hub_removed_flat_verbs_are_unknown() -> None:
    """Flat verbs removed in issue #105 no longer name a command in `--help` (matched by
    each line's own first token, not a substring), and invoking one fails with click's
    unknown-command error rather than silently delegating."""
    result = CliRunner().invoke(blizzard, ["hub", "--help"])
    assert result.exit_code == 0
    listed = {line.split()[0] for line in result.output.splitlines() if line.startswith("  ") and line.split()}
    for verb in ("answer", "ingest", "promote", "requeue", "decisions", "decide", "pause-chunk", "resume-chunk"):
        assert verb not in listed

    invoked = CliRunner().invoke(blizzard, ["hub", "promote", "ch_42"])
    assert invoked.exit_code != 0
    assert "No such command 'promote'" in invoked.output


def test_runner_lists_its_verbs() -> None:
    result = CliRunner().invoke(blizzard, ["runner", "--help"])
    assert result.exit_code == 0
    for verb in ("init", "migrate", "host", "heartbeat", "ask", "takeover", "requeue", "transcript"):
        assert verb in result.output


def test_hub_init_and_migrate(tmp_path: Path) -> None:
    runner = CliRunner()
    root = str(tmp_path / "hub")

    init_result = runner.invoke(blizzard, ["hub", "init", root])
    assert init_result.exit_code == 0, init_result.output
    assert (tmp_path / "hub" / "blizzard-hub.toml").exists()

    migrate_result = runner.invoke(blizzard, ["hub", "migrate", "--dir", root])
    assert migrate_result.exit_code == 0, migrate_result.output


def test_hub_migrate_rejects_a_leftover_pm_source_block(tmp_path: Path) -> None:
    """`migrate` — not just `host` — must reject the pre-rename key (issue #55): the
    dogfooding deploy runs `migrate` before `systemctl restart`, so a `host`-only guard
    would pass migrate, take the hub down at restart, and the runner with it."""
    root = tmp_path / "hub"
    runner = CliRunner()
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
    """issue #234: `cp -r <live-store>/* <copy>/ && blizzard hub migrate --dir <copy>`
    must refuse rather than silently migrate the live store — an absolute db_url is
    written in directly to model an unpatched-era init or an explicit override."""
    runner = CliRunner()
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
    runner = CliRunner()
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
    runner = CliRunner()
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
    runner = CliRunner()
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


def test_runner_init(tmp_path: Path) -> None:
    root = str(tmp_path / "runner")
    result = CliRunner().invoke(blizzard, ["runner", "init", root])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "runner" / "blizzard-runner.toml").exists()


# The runtime-dir env fallback (issue #39): --dir > $BZ_<daemon>_DIR > cwd. Parametrized
# over both daemons — a fallback wired on one but not the other is the drift worth catching.
_DAEMONS = [("hub", "BZ_HUB_DIR", "blizzard-hub.toml"), ("runner", "BZ_RUNNER_DIR", "blizzard-runner.toml")]


@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_dir_resolves_from_env_when_flag_absent(daemon: str, env_var: str, config_name: str, tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    cli = CliRunner()
    assert cli.invoke(blizzard, [daemon, "init", str(root)], env={env_var: None}).exit_code == 0

    # No --dir: the env names the runtime root, and `migrate` finds the store there.
    result = cli.invoke(blizzard, [daemon, "migrate"], env={env_var: str(root)})
    assert result.exit_code == 0, result.output
    assert "migrated" in result.output


@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_dir_flag_beats_env(daemon: str, env_var: str, config_name: str, tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    cli = CliRunner()
    assert cli.invoke(blizzard, [daemon, "init", str(root)], env={env_var: None}).exit_code == 0

    # The env names a dir that was never initialized, so this only succeeds if --dir wins.
    result = cli.invoke(blizzard, [daemon, "migrate", "--dir", str(root)], env={env_var: str(tmp_path / "unused")})
    assert result.exit_code == 0, result.output
    assert "migrated" in result.output


@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_dir_defaults_to_cwd_when_neither_set(
    daemon: str, env_var: str, config_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = CliRunner()
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
    result = CliRunner().invoke(blizzard, [daemon, "init"], env={env_var: str(root)})
    assert result.exit_code == 0, result.output
    assert (root / config_name).exists()


@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_dir_help_names_the_env_fallback(daemon: str, env_var: str, config_name: str) -> None:
    result = CliRunner().invoke(blizzard, [daemon, "migrate", "--help"])
    assert result.exit_code == 0
    assert f"${env_var}" in result.output


# `host` accepting a positional DIRECTORY like `init` does (issue #3): the config-load
# guard fails fast, before serving, naming the resolved directory — proof with nothing started.
@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_host_accepts_positional_directory(daemon: str, env_var: str, config_name: str, tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    result = CliRunner().invoke(blizzard, [daemon, "host", str(root)], env={env_var: None})
    assert result.exit_code != 0
    assert str(root) in result.output
    assert "serving blizzard" not in result.output


@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_host_dir_option_still_works(daemon: str, env_var: str, config_name: str, tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    result = CliRunner().invoke(blizzard, [daemon, "host", "--dir", str(root)], env={env_var: None})
    assert result.exit_code != 0
    assert str(root) in result.output
    assert "serving blizzard" not in result.output


@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_host_positional_and_dir_option_agreeing(daemon: str, env_var: str, config_name: str, tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    result = CliRunner().invoke(blizzard, [daemon, "host", str(root), "--dir", str(root)], env={env_var: None})
    assert result.exit_code != 0
    assert str(root) in result.output
    assert "disagree" not in result.output


@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_host_positional_and_dir_option_conflict(daemon: str, env_var: str, config_name: str, tmp_path: Path) -> None:
    positional = tmp_path / "positional"
    flagged = tmp_path / "flagged"
    result = CliRunner().invoke(blizzard, [daemon, "host", str(positional), "--dir", str(flagged)], env={env_var: None})
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
    result = CliRunner().invoke(blizzard, [daemon, "host", str(positional)], env={env_var: str(ambient)})
    assert result.exit_code != 0
    assert str(positional) in result.output
    assert "disagree" not in result.output


def test_hub_host_help_shows_directory_argument() -> None:
    result = CliRunner().invoke(blizzard, ["hub", "host", "--help"])
    assert result.exit_code == 0
    assert "DIRECTORY" in result.output


def test_runner_status_errors_cleanly_with_no_daemon_serving(tmp_path: Path) -> None:
    # `status` is a pure client of the local API (issue #51), same as `pause`/`start` —
    # no socket, no store fallback, a clean error naming the missing daemon.
    root = tmp_path / "runner"
    assert CliRunner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0

    result = CliRunner().invoke(blizzard, ["runner", "status", "--dir", str(root)])

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
    assert CliRunner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0
    RunnerConfig.socket_path_for(root).write_bytes(b"")

    assert _daemon_holding(RunnerConfig.load(root)) is None


def test_a_daemon_answering_its_socket_is_holding_the_store(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    assert CliRunner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0
    server = _serve_on(RunnerConfig.socket_path_for(root), _OK)

    try:
        assert _daemon_holding(RunnerConfig.load(root)) is not None
    finally:
        server.close()


@pytest.mark.parametrize(("reply", "expected"), [(None, "did not answer"), (_UNAVAILABLE, "503")])
def test_an_ambiguous_liveness_answer_fails_closed(reply: bytes | None, expected: str, tmp_path: Path) -> None:
    """A wedged or unhealthy daemon still HOLDS the single-writer store. Resolving either
    ambiguity toward "nothing there" would let the verb write concurrently with it."""
    root = tmp_path / "runner"
    assert CliRunner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0
    server = _serve_on(RunnerConfig.socket_path_for(root), reply)

    try:
        holding = _daemon_holding(RunnerConfig.load(root))
    finally:
        server.close()

    assert holding is not None and expected in holding


def test_runner_transcript_backfill_refuses_while_the_lane_is_off(tmp_path: Path) -> None:
    # `[transcripts] ship` is false in a scaffolded config (issue #246, D5), and a backfill
    # into a lane the operator has switched off would ship content they never enabled.
    root = tmp_path / "runner"
    assert CliRunner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0

    result = CliRunner().invoke(blizzard, ["runner", "transcript", "backfill", "--dir", str(root)])

    assert result.exit_code != 0
    assert "[transcripts] ship is false" in result.output


def _enable_transcript_shipping(root: Path) -> None:
    config_path = root / "blizzard-runner.toml"
    config_path.write_text(config_path.read_text().replace("ship = false", "ship = true"))


def test_runner_transcript_backfill_drives_its_whole_production_route(tmp_path: Path) -> None:
    """`bzh:gating-tier-pins-production-paths`: the component tier constructs the service
    directly, so without this the composition-root wiring the operator actually runs
    (CLI -> LoopWiring.backfill_transcripts -> the report line) is named by no gating test."""
    root = tmp_path / "runner"
    assert CliRunner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0
    _enable_transcript_shipping(root)

    result = CliRunner().invoke(blizzard, ["runner", "transcript", "backfill", "--dir", str(root), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "would import 0, already present 0, gone 0" in result.output


def test_runner_transcript_backfill_refuses_while_a_daemon_holds_the_store(tmp_path: Path) -> None:
    """The single-writer guard, exercised through the verb rather than the helper alone."""
    root = tmp_path / "runner"
    assert CliRunner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0
    _enable_transcript_shipping(root)
    server = _serve_on(RunnerConfig.socket_path_for(root), _OK)

    try:
        result = CliRunner().invoke(blizzard, ["runner", "transcript", "backfill", "--dir", str(root)])
    finally:
        server.close()

    assert result.exit_code != 0
    assert "single-writer" in result.output


def test_runner_takeover_errors_cleanly_with_no_daemon_serving(tmp_path: Path) -> None:
    # `takeover` is a pure client of the local API too (issue #52) — no socket, no store
    # fallback, a clean error naming the missing daemon.
    root = tmp_path / "runner"
    assert CliRunner().invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0

    result = CliRunner().invoke(blizzard, ["runner", "takeover", "ch_1", "--dir", str(root)])

    assert result.exit_code != 0
    assert "no runner daemon is serving" in result.output


def test_hub_host_refuses_a_db_url_copied_from_elsewhere(tmp_path: Path) -> None:
    """`host` applies the same --dir isolation guard as `migrate` (issue #234) — it
    fails before ever announcing "serving", let alone binding a socket."""
    runner = CliRunner()
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


def test_hub_host_reports_an_unset_work_source_token_env_as_a_clean_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `[[work_source]]` naming an unset `token_env` fails at boot as the same
    clean CLI error the config-load guard raises — not an unhandled traceback; the
    boot failure is by design, the traceback was not."""
    runner = CliRunner()
    root = tmp_path / "hub"
    assert runner.invoke(blizzard, ["hub", "init", str(root)]).exit_code == 0
    monkeypatch.delenv("BZ_WORK_SOURCE_TOKEN", raising=False)
    (root / "blizzard-hub.toml").write_text(
        (root / "blizzard-hub.toml").read_text() + '\n[[work_source]]\nname = "blizzard"\nprovider = "github"\n'
        'repo = "paul-gross/blizzard"\ntoken_env = "BZ_WORK_SOURCE_TOKEN"\n'
    )

    result = runner.invoke(blizzard, ["hub", "host", "--dir", str(root)])

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "BZ_WORK_SOURCE_TOKEN" in result.output  # names the variable the operator must set
    # It never claims to be serving a daemon it then fails to build.
    assert "serving blizzard-hub" not in result.output


def test_runner_host_reports_a_missing_runner_prompt_file_as_a_clean_error(tmp_path: Path) -> None:
    """A configured-but-missing ``runner_prompt_file`` (issue #103) fails at boot as a
    clean CLI error: ``PeriodicDriver`` resolves it before any socket binds, rather than
    silently killing the reconciliation loop while uvicorn keeps serving."""
    runner = CliRunner()
    root = tmp_path / "runner"
    assert runner.invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0
    config_path = root / "blizzard-runner.toml"
    config_path.write_text(
        config_path.read_text().replace('runner_prompt_file = ""', 'runner_prompt_file = "does-not-exist.md"')
    )

    result = runner.invoke(blizzard, ["runner", "host", "--dir", str(root)])

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "does-not-exist.md" in result.output
    # It never claims to be serving a daemon it then fails to build.
    assert "serving blizzard-runner" not in result.output
