"""``blizzard runner init``'s hub step — keep the runner a held token names, or add one and write
its token, and stop with nothing added wherever adding could undo an operator's act or mint a runner
at the wrong hub. Driven in-process against the autouse ``fake_init_hub``."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

import pytest
from click.testing import Result

from blizzard.cli.main import blizzard
from blizzard.foundation.operator_sessions.internal.session_file import SessionFile
from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.runner.config import CONFIG_FILENAME, DEFAULT_TOKEN_ENV, RunnerConfig
from blizzard.runner.hub.bootstrap import BootstrapStop, BootstrapStopped, HeldToken, kept_identity
from blizzard.runner.hub.client import (
    HubClientError,
    RunnerAddRefusalReason,
    RunnerAddRefused,
    TokenIdentity,
    TokenRefusal,
)
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.hub.token_file import ENV_FILENAME, assigned_token, with_token
from tests.runner_init_fakes import FakeInitHub, FakeTokenIdentityReader, init_runner

_HUB = "http://hub.test:8421"
_FROM_FILE = HeldToken("t", from_process_env=False)


def _init(hub: FakeInitHub, root: Path, *args: str) -> Result:
    return init_runner(hub).invoke(blizzard, ["runner", "init", str(root), "--hub", _HUB, *args])


def _joined(tmp_path: Path, fake_init_hub: FakeInitHub) -> tuple[Path, bytes]:
    """A runner init added at the fake hub, with an unrelated line in its ``.env`` — its runtime dir
    and the ``.env`` bytes init left."""
    root = tmp_path / "runner"
    root.mkdir()
    (root / ENV_FILENAME).write_text("OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318\n")
    result = _init(fake_init_hub, root)
    assert result.exit_code == 0, result.output
    assert [i.runner_id for i in fake_init_hub.admin.added] == ["rn_init1"]
    return root, (root / ENV_FILENAME).read_bytes()


# --- the verdict, by value -----------------------------------------------------------------------


@pytest.mark.unit
def test_a_token_the_hub_names_is_kept() -> None:
    identity = TokenIdentity(runner_id="rn_1", runner_name="r-claude")
    assert kept_identity(identity, held=_FROM_FILE, allow_readd=False) == identity


@pytest.mark.unit
def test_an_unknown_token_stops_unless_a_readd_is_allowed() -> None:
    unknown = TokenRefusal(reason=RunnerTokenRefusalReason.UNKNOWN)
    with pytest.raises(BootstrapStopped) as stopped:
        kept_identity(unknown, held=_FROM_FILE, allow_readd=False)
    assert stopped.value.stop is BootstrapStop.UNKNOWN_TOKEN
    assert kept_identity(unknown, held=_FROM_FILE, allow_readd=True) is None


@pytest.mark.unit
def test_an_unknown_token_the_environment_supplies_stops_even_with_a_readd_allowed() -> None:
    unknown = TokenRefusal(reason=RunnerTokenRefusalReason.UNKNOWN)
    with pytest.raises(BootstrapStopped) as stopped:
        kept_identity(unknown, held=HeldToken("t", from_process_env=True), allow_readd=True)
    assert stopped.value.stop is BootstrapStop.TOKEN_OVERRIDDEN


@pytest.mark.unit
@pytest.mark.parametrize(
    ("reason", "stop"),
    [
        (RunnerTokenRefusalReason.REVOKED, BootstrapStop.REVOKED_TOKEN),
        (RunnerTokenRefusalReason.RETIRED, BootstrapStop.RETIRED_RUNNER),
    ],
)
def test_a_revoked_token_or_a_retired_runner_stops_naming_the_runner_even_with_a_readd_allowed(
    reason: RunnerTokenRefusalReason, stop: BootstrapStop
) -> None:
    with pytest.raises(BootstrapStopped) as stopped:
        kept_identity(TokenRefusal(reason=reason, runner_id="rn_1"), held=_FROM_FILE, allow_readd=True)
    assert (stopped.value.stop, stopped.value.runner_id) == (stop, "rn_1")


@pytest.mark.unit
def test_a_token_the_hub_never_received_stops() -> None:
    with pytest.raises(BootstrapStopped) as stopped:
        kept_identity(TokenRefusal(reason=RunnerTokenRefusalReason.MISSING), held=_FROM_FILE, allow_readd=True)
    assert stopped.value.stop is BootstrapStop.TOKEN_NOT_RECEIVED


# --- the token file ------------------------------------------------------------------------------


@pytest.mark.unit
def test_the_token_file_replaces_only_the_token_line_owner_only(tmp_path: Path) -> None:
    env = tmp_path / ENV_FILENAME
    env.write_text("# instance\nOTEL_EXPORTER_OTLP_ENDPOINT=http://c:4318\nBZ_HUB_TOKEN=old\nBZ_HUB_TOKEN=older\n")
    tokens = HubTokenFile.of(tmp_path, DEFAULT_TOKEN_ENV)
    assert tokens.held() == "older"  # the last assignment wins, as systemd reads it
    tokens.write("new")
    assert env.read_text() == "# instance\nOTEL_EXPORTER_OTLP_ENDPOINT=http://c:4318\nBZ_HUB_TOKEN=new\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == [ENV_FILENAME]


@pytest.mark.unit
def test_the_token_file_reads_a_quoted_token_and_holds_none_when_absent(tmp_path: Path) -> None:
    tokens = HubTokenFile.of(tmp_path, DEFAULT_TOKEN_ENV)
    assert tokens.held() == ""
    (tmp_path / ENV_FILENAME).write_text('BZ_HUB_TOKEN="quoted"\n')
    assert tokens.held() == "quoted"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ""),
        ("# BZ_HUB_TOKEN=commented\n; BZ_HUB_TOKEN=also\nOTHER=x\n", ""),
        ("BZ_HUB_TOKEN=old\nBZ_HUB_TOKEN='last'\n", "last"),
        ('  BZ_HUB_TOKEN = "spaced" \n', "spaced"),
    ],
)
def test_the_token_a_text_assigns_is_its_last_unquoted_assignment(text: str, expected: str) -> None:
    assert assigned_token(text, "BZ_HUB_TOKEN") == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", "BZ_HUB_TOKEN=new\n"),
        ("A=1\n", "A=1\nBZ_HUB_TOKEN=new\n"),
        ("A=1\nBZ_HUB_TOKEN=x\n# keep\nBZ_HUB_TOKEN=y\n", "A=1\nBZ_HUB_TOKEN=new\n# keep\n"),
    ],
)
def test_a_text_with_the_token_replaced_keeps_every_other_line(text: str, expected: str) -> None:
    assert with_token(text, "BZ_HUB_TOKEN", "new") == expected


@pytest.mark.component
def test_a_runner_verb_reads_the_token_file_and_the_environment_overrides_it(
    fake_init_hub: FakeInitHub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "runner"
    assert _init(fake_init_hub, root).exit_code == 0
    assert RunnerConfig.load(root).hub_token == "init-token-1"
    monkeypatch.setenv(DEFAULT_TOKEN_ENV, "from-env")
    assert RunnerConfig.load(root).hub_token == "from-env"


# --- init against the hub ------------------------------------------------------------------------


@pytest.mark.component
def test_init_adds_the_runner_under_its_name_and_writes_its_token(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = tmp_path / "runner"
    result = _init(fake_init_hub, root)
    assert result.exit_code == 0, result.output
    assert [(i.runner_id, i.runner_name) for i in fake_init_hub.admin.added] == [("rn_init1", "runner-local")]
    assert [(c.hub_url, c.runner_token, c.operator_token) for c in fake_init_hub.calls] == [(_HUB, "", None)]
    env = root / ENV_FILENAME
    assert env.read_text() == "BZ_HUB_TOKEN=init-token-1\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert "rn_init1" in result.output


@pytest.mark.component
def test_init_reuses_a_token_the_hub_accepts_and_adds_nothing(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root, env_bytes = _joined(tmp_path, fake_init_hub)
    result = _init(fake_init_hub, root)
    assert result.exit_code == 0, result.output
    assert len(fake_init_hub.admin.added) == 1
    assert fake_init_hub.calls[-1].runner_token == "init-token-1"
    assert (root / ENV_FILENAME).read_bytes() == env_bytes


@pytest.mark.component
def test_init_keeping_a_hand_written_token_makes_its_file_owner_only_bytes_unchanged(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    """An operator adds the runner elsewhere and pastes its token line under the default umask."""
    issued = fake_init_hub.admin.add_runner("anna-laptop")
    root = tmp_path / "runner"
    root.mkdir()
    env = root / ENV_FILENAME
    env.write_text(f"OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318\n{DEFAULT_TOKEN_ENV}={issued.token}\n")
    env.chmod(0o644)
    env_bytes = env.read_bytes()
    result = _init(fake_init_hub, root)
    assert result.exit_code == 0, result.output
    assert "keeps its token" in result.output and "owner-only" in result.output
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert env.read_bytes() == env_bytes
    assert len(fake_init_hub.admin.added) == 1


@pytest.mark.component
def test_init_that_cannot_restrict_a_kept_token_file_warns_naming_it(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _joined(tmp_path, fake_init_hub)
    (root / ENV_FILENAME).chmod(0o644)

    def owned_by_another_account(self: HubTokenFile) -> bool:
        raise PermissionError(errno.EPERM, os.strerror(errno.EPERM), str(self.path))

    monkeypatch.setattr(HubTokenFile, "restrict", owned_by_another_account)
    result = _init(fake_init_hub, root)
    assert result.exit_code == 0, result.output
    assert f"warning: other accounts may read {root / ENV_FILENAME}" in result.output
    assert f"chmod 600 {root / ENV_FILENAME}" in result.output


@pytest.mark.unit
@pytest.mark.parametrize(("content", "mode"), [("OTEL_SERVICE_NAME=r\n", 0o644), ("BZ_HUB_TOKEN=t\n", 0o600)])
def test_the_token_file_leaves_a_file_holding_no_token_or_already_owner_only_as_it_is(
    tmp_path: Path, content: str, mode: int
) -> None:
    env = tmp_path / ENV_FILENAME
    env.write_text(content)
    env.chmod(mode)
    assert HubTokenFile.of(tmp_path, DEFAULT_TOKEN_ENV).restrict() is False
    assert stat.S_IMODE(env.stat().st_mode) == mode
    assert HubTokenFile.of(tmp_path / "absent", DEFAULT_TOKEN_ENV).restrict() is False


@pytest.mark.unit
def test_the_token_file_raises_when_it_cannot_read_a_present_files_mode(tmp_path: Path) -> None:
    """Only an absent file is nothing to restrict; one present but unreadable is reported."""
    (tmp_path / ENV_FILENAME).symlink_to(ENV_FILENAME)  # a link to itself: present, never stat-able
    with pytest.raises(OSError, match="symbolic links"):
        HubTokenFile.of(tmp_path, DEFAULT_TOKEN_ENV).restrict()


@pytest.mark.component
def test_init_with_a_wiped_store_and_a_current_token_adds_nothing(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root, _ = _joined(tmp_path, fake_init_hub)
    for path in RunnerConfig.load(root).data_dir.iterdir():
        path.unlink()
    result = _init(fake_init_hub, root)
    assert result.exit_code == 0, result.output
    assert len(fake_init_hub.admin.added) == 1


@pytest.mark.component
def test_init_after_a_hub_reset_re_adds_the_runner_with_allow_readd(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root, _ = _joined(tmp_path, fake_init_hub)
    fake_init_hub.admin.added.clear()  # the hub's data is reset; the runtime dir survives
    result = _init(fake_init_hub, root, "--allow-readd")
    assert result.exit_code == 0, result.output
    assert [i.runner_id for i in fake_init_hub.admin.added] == ["rn_init1"]
    assert (root / ENV_FILENAME).read_text() == (
        "OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318\nBZ_HUB_TOKEN=init-token-1\n"
    )


@pytest.mark.component
def test_init_after_a_hub_reset_stops_without_allow_readd_leaving_the_env_file_as_it_was(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root, env_bytes = _joined(tmp_path, fake_init_hub)
    fake_init_hub.admin.added.clear()
    result = _init(fake_init_hub, root)
    assert result.exit_code != 0
    assert _HUB in result.output
    assert "--allow-readd" in result.output
    assert fake_init_hub.admin.added == []
    assert (root / ENV_FILENAME).read_bytes() == env_bytes


_ENROLL = "blizzard hub runner enroll rn_init1"
_REINSTATE = "blizzard hub runner reinstate rn_init1"


@pytest.mark.component
@pytest.mark.parametrize(
    ("reason", "verbs"),
    [(RunnerTokenRefusalReason.REVOKED, [_ENROLL]), (RunnerTokenRefusalReason.RETIRED, [_REINSTATE, _ENROLL])],
)
def test_init_stops_on_a_revoked_token_or_a_retired_runner_naming_each_verb_and_where_the_new_token_goes(
    tmp_path: Path, fake_init_hub: FakeInitHub, reason: RunnerTokenRefusalReason, verbs: list[str]
) -> None:
    """Retiring revokes the token, so a reinstated runner still needs a new one before init keeps it."""
    root, env_bytes = _joined(tmp_path, fake_init_hub)
    fake_init_hub.identity = FakeTokenIdentityReader(TokenRefusal(reason=reason, runner_id="rn_init1"))
    result = _init(fake_init_hub, root, "--allow-readd")
    assert result.exit_code != 0
    assert all(verb in result.output for verb in verbs)
    assert result.output.find(verbs[0]) <= result.output.find(verbs[-1])
    assert f"put it in {root / ENV_FILENAME} as {DEFAULT_TOKEN_ENV}" in result.output
    assert "re-run init" not in result.output
    assert len(fake_init_hub.admin.added) == 1
    assert (root / ENV_FILENAME).read_bytes() == env_bytes


@pytest.mark.component
@pytest.mark.parametrize("reason", [RunnerTokenRefusalReason.REVOKED, RunnerTokenRefusalReason.RETIRED])
def test_init_stopping_on_a_token_the_environment_supplies_says_to_replace_the_variable(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch, reason: RunnerTokenRefusalReason
) -> None:
    """A new token written to ``.env`` would go unread while the variable is set."""
    root, env_bytes = _joined(tmp_path, fake_init_hub)
    monkeypatch.setenv(DEFAULT_TOKEN_ENV, "revoked-in-env")
    fake_init_hub.identity = FakeTokenIdentityReader(TokenRefusal(reason=reason, runner_id="rn_init1"))
    result = _init(fake_init_hub, root)
    assert result.exit_code != 0
    assert fake_init_hub.calls[-1].runner_token == "revoked-in-env"
    assert f"set {DEFAULT_TOKEN_ENV} to it, or unset {DEFAULT_TOKEN_ENV} and put it in {root / ENV_FILENAME}" in (
        result.output
    )
    assert (root / ENV_FILENAME).read_bytes() == env_bytes


@pytest.mark.component
def test_init_fails_and_adds_nothing_when_the_hub_is_unreachable(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root, env_bytes = _joined(tmp_path, fake_init_hub)
    fake_init_hub.identity = FakeTokenIdentityReader(HubClientError("GET /api/fleet/identity failed: refused"))
    result = _init(fake_init_hub, root, "--allow-readd")
    assert result.exit_code != 0
    assert _HUB in result.output
    assert len(fake_init_hub.admin.added) == 1
    assert (root / ENV_FILENAME).read_bytes() == env_bytes


@pytest.mark.component
def test_init_with_no_token_fails_and_writes_nothing_when_the_add_cannot_reach_the_hub(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    fake_init_hub.admin.refusal = HubClientError("POST /api/runners -> 503")
    result = _init(fake_init_hub, tmp_path / "runner")
    assert result.exit_code != 0
    assert not (tmp_path / "runner" / ENV_FILENAME).exists()


@pytest.mark.component
def test_init_that_cannot_write_an_added_runners_token_fails_naming_the_runner_to_retire(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unwritable(self: HubTokenFile, token: str) -> None:
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(self.path))

    monkeypatch.setattr(HubTokenFile, "write", unwritable)
    result = _init(fake_init_hub, tmp_path / "runner")
    assert result.exit_code != 0
    assert [i.runner_id for i in fake_init_hub.admin.added] == ["rn_init1"]
    assert f"retire it with `blizzard hub runner retire rn_init1 --hub-url {_HUB}`" in result.output


@pytest.mark.component
def test_init_against_a_hub_requiring_sign_in_says_to_log_in(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    fake_init_hub.admin.refusal = RunnerAddRefused(
        "POST /api/runners -> 401", reason=RunnerAddRefusalReason.SIGN_IN_REQUIRED, detail="not authenticated"
    )
    result = _init(fake_init_hub, tmp_path / "runner")
    assert result.exit_code != 0
    assert "blizzard hub login" in result.output
    assert not (tmp_path / "runner" / ENV_FILENAME).exists()


@pytest.mark.component
def test_init_adds_under_the_operator_session_held_for_its_hub(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    SessionFile.of().save(f"{_HUB}/", "operator-session")
    assert _init(fake_init_hub, tmp_path / "runner").exit_code == 0
    assert fake_init_hub.calls[-1].operator_token == "operator-session"


@pytest.mark.component
def test_init_refuses_an_existing_config_naming_another_hub(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root, _ = _joined(tmp_path, fake_init_hub)
    result = init_runner(fake_init_hub).invoke(
        blizzard, ["runner", "init", str(root), "--hub", "http://elsewhere:8421"]
    )
    assert result.exit_code != 0
    assert "http://elsewhere:8421" in result.output
    assert len(fake_init_hub.calls) == 1
    assert RunnerConfig.load(root).hub_url == _HUB


@pytest.mark.component
def test_init_stops_when_the_environment_overrides_a_token_it_must_replace(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, env_bytes = _joined(tmp_path, fake_init_hub)
    monkeypatch.setenv(DEFAULT_TOKEN_ENV, "stale-from-env")
    result = _init(fake_init_hub, root, "--allow-readd")
    assert result.exit_code != 0
    assert DEFAULT_TOKEN_ENV in result.output
    assert len(fake_init_hub.admin.added) == 1
    assert (root / ENV_FILENAME).read_bytes() == env_bytes


@pytest.mark.component
def test_init_adds_a_legacy_runner_id_only_config_under_that_id_as_its_name(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = tmp_path / "runner"
    assert _init(fake_init_hub, root).exit_code == 0
    config = root / CONFIG_FILENAME
    config.write_text(config.read_text().replace('name = "runner-local"', 'runner_id = "r-legacy"'))
    (root / ENV_FILENAME).unlink()
    result = _init(fake_init_hub, root)
    assert result.exit_code == 0, result.output
    assert fake_init_hub.admin.added[-1].runner_name == "r-legacy"
