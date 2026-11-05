"""``blizzard runner init``'s hub step — keep the runner a held token names, or add one and write
its token, and stop with nothing added wherever adding could undo an operator's act or mint a runner
at the wrong hub. Driven in-process against the autouse ``fake_init_hub``."""

from __future__ import annotations

import stat
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.runner.hub.bootstrap import (
    BootstrapStop,
    BootstrapStopped,
    HeldToken,
    JoinedRunner,
    RunnerBootstrap,
    kept_identity,
    refuse_tokenless,
    refuse_unplaced,
)
from blizzard.runner.hub.client import (
    IssuedIdentity,
    TokenIdentity,
    TokenRefusal,
)
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.hub.token_file import ENV_FILENAME, assigned_token, with_token
from tests.support.runner_init_fakes import FakeHubRunnerAdmin, FakeTokenIdentityReader
from tests.support.runner_join import DEFAULT_TOKEN_ENV

_FROM_FILE = HeldToken("t", from_process_env=False)


def test_a_token_the_hub_names_is_kept() -> None:
    identity = TokenIdentity(runner_id="rn_1", runner_name="r-claude")
    assert kept_identity(identity, held=_FROM_FILE, allow_readd=False) == identity


def test_an_unknown_token_stops_unless_a_readd_is_allowed() -> None:
    unknown = TokenRefusal(reason=RunnerTokenRefusalReason.UNKNOWN)
    with pytest.raises(BootstrapStopped) as stopped:
        kept_identity(unknown, held=_FROM_FILE, allow_readd=False)
    assert stopped.value.stop is BootstrapStop.UNKNOWN_TOKEN
    assert kept_identity(unknown, held=_FROM_FILE, allow_readd=True) is None


def test_an_unknown_token_the_environment_supplies_stops_even_with_a_readd_allowed() -> None:
    unknown = TokenRefusal(reason=RunnerTokenRefusalReason.UNKNOWN)
    with pytest.raises(BootstrapStopped) as stopped:
        kept_identity(unknown, held=HeldToken("t", from_process_env=True), allow_readd=True)
    assert stopped.value.stop is BootstrapStop.TOKEN_OVERRIDDEN


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


def test_a_token_the_hub_never_received_stops() -> None:
    with pytest.raises(BootstrapStopped) as stopped:
        kept_identity(TokenRefusal(reason=RunnerTokenRefusalReason.MISSING), held=_FROM_FILE, allow_readd=True)
    assert stopped.value.stop is BootstrapStop.TOKEN_NOT_RECEIVED


def test_a_refusal_reason_this_build_does_not_know_stops_carrying_it_raw() -> None:
    refusal = TokenRefusal(reason=Unrecognized("suspended"), runner_id="rn_1")
    with pytest.raises(BootstrapStopped) as stopped:
        kept_identity(refusal, held=_FROM_FILE, allow_readd=True)
    assert (stopped.value.stop, stopped.value.runner_id, stopped.value.reason) == (
        BootstrapStop.TOKEN_REFUSED,
        "rn_1",
        "suspended",
    )


def test_a_token_naming_another_runner_than_the_configured_one_stops_unless_a_readd_is_allowed() -> None:
    other = TokenIdentity(runner_id="rn_2", runner_name="r-claude", tenant_id="tn_b")
    with pytest.raises(BootstrapStopped) as stopped:
        kept_identity(other, held=_FROM_FILE, allow_readd=False, tenant_id="tn_a", configured_id="rn_1")
    assert (stopped.value.stop, stopped.value.runner_id, stopped.value.held) == (
        BootstrapStop.ANOTHER_RUNNER,
        "rn_1",
        other,
    )
    assert kept_identity(other, held=_FROM_FILE, allow_readd=True, configured_id="rn_1") == other
    assert kept_identity(other, held=_FROM_FILE, allow_readd=False, configured_id="rn_2") == other


def test_a_runner_serves_the_tenant_named_and_any_when_either_names_none() -> None:
    runner = TokenIdentity(runner_id="rn_1", runner_name="r", tenant_id="tn_a")
    assert runner.serves("tn_a") and runner.serves(None)
    assert not runner.serves("tn_b")
    assert TokenIdentity(runner_id="rn_1", runner_name="r").serves("tn_b")


def test_no_token_stops_for_the_runner_the_store_names_then_the_configured_one_unless_a_readd_is_allowed() -> None:
    with pytest.raises(BootstrapStopped) as stored:
        refuse_tokenless(None, "rn_1", "rn_old", allow_readd=False)
    assert (stored.value.stop, stored.value.runner_id) == (BootstrapStop.STORE_NAMES_A_RUNNER, "rn_old")
    with pytest.raises(BootstrapStopped) as configured:
        refuse_tokenless(None, "rn_1", None, allow_readd=False)
    assert (configured.value.stop, configured.value.runner_id) == (BootstrapStop.TOKENLESS_CONFIGURED, "rn_1")
    refuse_tokenless(None, "rn_1", "rn_old", allow_readd=True)
    refuse_tokenless(_FROM_FILE, "rn_1", "rn_old", allow_readd=False)
    refuse_tokenless(None, None, None, allow_readd=False)


def test_the_token_file_replaces_only_the_token_line_owner_only(tmp_path: Path) -> None:
    env = tmp_path / ENV_FILENAME
    env.write_text(
        f"# instance\nOTEL_EXPORTER_OTLP_ENDPOINT=http://c:4318\n{DEFAULT_TOKEN_ENV}=old\n{DEFAULT_TOKEN_ENV}=older\n"
    )
    tokens = HubTokenFile.of(tmp_path, DEFAULT_TOKEN_ENV)
    assert tokens.held() == "older"  # the last assignment wins, as systemd reads it
    tokens.write("new")
    assert env.read_text() == f"# instance\nOTEL_EXPORTER_OTLP_ENDPOINT=http://c:4318\n{DEFAULT_TOKEN_ENV}=new\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == [ENV_FILENAME]


def test_the_token_file_reads_a_quoted_token_and_holds_none_when_absent(tmp_path: Path) -> None:
    tokens = HubTokenFile.of(tmp_path, DEFAULT_TOKEN_ENV)
    assert tokens.held() == ""
    (tmp_path / ENV_FILENAME).write_text(f'{DEFAULT_TOKEN_ENV}="quoted"\n')
    assert tokens.held() == "quoted"


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


@pytest.mark.parametrize(("content", "mode"), [("OTEL_SERVICE_NAME=r\n", 0o644), (f"{DEFAULT_TOKEN_ENV}=t\n", 0o600)])
def test_the_token_file_leaves_a_file_holding_no_token_or_already_owner_only_as_it_is(
    tmp_path: Path, content: str, mode: int
) -> None:
    env = tmp_path / ENV_FILENAME
    env.write_text(content)
    env.chmod(mode)
    assert HubTokenFile.of(tmp_path, DEFAULT_TOKEN_ENV).restrict() is False
    assert stat.S_IMODE(env.stat().st_mode) == mode
    assert HubTokenFile.of(tmp_path / "absent", DEFAULT_TOKEN_ENV).restrict() is False


def test_the_token_file_raises_when_it_cannot_read_a_present_files_mode(tmp_path: Path) -> None:
    """Only an absent file is nothing to restrict; one present but unreadable is reported."""
    (tmp_path / ENV_FILENAME).symlink_to(ENV_FILENAME)  # a link to itself: present, never stat-able
    with pytest.raises(OSError, match="symbolic links"):
        HubTokenFile.of(tmp_path, DEFAULT_TOKEN_ENV).restrict()


@dataclass
class _JoinLog:
    """Records, in order, each store and add call a join makes."""

    stored: str | None = None
    calls: list[str] = field(default_factory=list[str])

    def stored_runner_id(self) -> str | None:
        return self.stored

    def is_set_aside(self) -> bool:
        return False

    def sharing(self) -> tuple[str, ...]:
        return ()

    def set_aside(self) -> None:
        self.calls.append("set_aside")


@dataclass
class _LoggedAdmin(FakeHubRunnerAdmin):
    log: _JoinLog = field(default_factory=_JoinLog)

    def add_runner(
        self, name: str, *, tenant_id: str | None = None, projects: tuple[str, ...] | None = None
    ) -> IssuedIdentity:
        self.log.calls.append("add")
        return super().add_runner(name, tenant_id=tenant_id, projects=projects)


class _Tokens:
    def write(self, token: str) -> None:
        pass


@dataclass
class _SharedStore(_JoinLog):
    def sharing(self) -> tuple[str, ...]:
        return ("r-two",)


def _join(
    log: _JoinLog,
    answer: TokenIdentity | TokenRefusal,
    *,
    held: HeldToken | None,
    allow_readd: bool,
    configured_id: str | None = None,
) -> None:
    RunnerBootstrap(FakeTokenIdentityReader(answer), _LoggedAdmin(log=log), _Tokens(), log).join(
        "r", held=held, allow_readd=allow_readd, configured_id=configured_id
    )


def test_a_join_holding_another_runners_token_stops_with_nothing_set_aside_or_added() -> None:
    log = _JoinLog(stored="rn_1")
    with pytest.raises(BootstrapStopped) as stopped:
        _join(
            log,
            TokenIdentity(runner_id="rn_2", runner_name="r"),
            held=_FROM_FILE,
            allow_readd=False,
            configured_id="rn_1",
        )
    assert (stopped.value.stop, stopped.value.runner_id) == (BootstrapStop.ANOTHER_RUNNER, "rn_1")
    assert log.calls == []


def test_a_join_of_a_configured_runner_holding_no_token_stops_with_nothing_added() -> None:
    log = _JoinLog()
    with pytest.raises(BootstrapStopped) as stopped:
        _join(
            log,
            TokenRefusal(reason=RunnerTokenRefusalReason.UNKNOWN),
            held=None,
            allow_readd=False,
            configured_id="rn_1",
        )
    assert (stopped.value.stop, stopped.value.runner_id) == (BootstrapStop.TOKENLESS_CONFIGURED, "rn_1")
    assert log.calls == []


def test_a_re_add_over_a_store_other_runners_hold_stops_naming_them_with_nothing_added() -> None:
    log = _SharedStore(stored="rn_old")
    with pytest.raises(BootstrapStopped) as stopped:
        _join(log, TokenRefusal(reason=RunnerTokenRefusalReason.UNKNOWN), held=_FROM_FILE, allow_readd=True)
    assert (stopped.value.stop, stopped.value.sharing) == (BootstrapStop.STORE_SHARED, ("r-two",))
    assert log.calls == []


def test_a_re_add_over_a_store_this_init_already_set_aside_is_not_judged_shared() -> None:
    @dataclass
    class _AlreadyAside(_SharedStore):
        def is_set_aside(self) -> bool:
            return True

    log = _AlreadyAside(stored="rn_old")
    _join(log, TokenRefusal(reason=RunnerTokenRefusalReason.UNKNOWN), held=_FROM_FILE, allow_readd=True)
    assert log.calls == ["add"]


def test_a_kept_runner_the_joined_host_does_not_hold_stops_naming_it() -> None:
    kept = JoinedRunner(runner_id="rn_1", runner_name="r", added=False)
    with pytest.raises(BootstrapStopped) as stopped:
        refuse_unplaced(kept, ("rn_2",))
    assert (stopped.value.stop, stopped.value.runner_id) == (BootstrapStop.NOT_UNDER_HOST, "rn_1")
    refuse_unplaced(kept, ("rn_1",))
    refuse_unplaced(JoinedRunner(runner_id="rn_9", runner_name="r", added=True), ())


def test_a_re_add_sets_the_old_store_aside_before_adding() -> None:
    log = _JoinLog(stored="rn_old")
    _join(log, TokenRefusal(reason=RunnerTokenRefusalReason.UNKNOWN), held=_FROM_FILE, allow_readd=True)
    assert log.calls == ["set_aside", "add"]


def test_a_kept_token_keeps_the_store() -> None:
    log = _JoinLog(stored="rn_1")
    _join(log, TokenIdentity(runner_id="rn_1", runner_name="r"), held=_FROM_FILE, allow_readd=True)
    assert log.calls == []


def test_no_token_over_a_store_naming_a_runner_stops_unless_a_readd_is_allowed() -> None:
    unknown = TokenRefusal(reason=RunnerTokenRefusalReason.UNKNOWN)
    with pytest.raises(BootstrapStopped) as stopped:
        _join(_JoinLog(stored="rn_old"), unknown, held=None, allow_readd=False)
    assert (stopped.value.stop, stopped.value.runner_id) == (BootstrapStop.STORE_NAMES_A_RUNNER, "rn_old")
    log = _JoinLog(stored="rn_old")
    _join(log, unknown, held=None, allow_readd=True)
    assert log.calls == ["set_aside", "add"]


def test_no_token_over_a_store_naming_no_runner_adds_without_setting_it_aside() -> None:
    log = _JoinLog()
    _join(log, TokenRefusal(reason=RunnerTokenRefusalReason.UNKNOWN), held=None, allow_readd=False)
    assert log.calls == ["add"]
