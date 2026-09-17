"""The OpenCode adapter — command composition (unit) and a real subprocess (component).

Mirrors ``tests/test_runner_harness_adapter.py``'s split: unit tests drive the command
builder and model/effort/compaction resolution against a monkeypatched ``subprocess.Popen``;
the component tests launch a real fake ``opencode`` binary through Phase 1's
``ProcessLauncher`` (``tests/support_opencode_binary.py::worker_binary``)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import HarnessSpawnError, WorkerIdentityError, WorkerPreamble
from blizzard.runner.harness.identity import OPENCODE_HARNESS_ID
from blizzard.runner.harness.internal.opencode_adapter import OpenCodeAdapter, _PendingOpenCodeIdentity
from blizzard.runner.harness.internal.opencode_command import OpenCodeCommand, OpenCodeInvocationKind
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.loop.process import LinuxProcessProbe
from blizzard.runner.loop.session import HarnessSelection, HarnessSelector, SkippedHarness
from tests.runner_fakes import FakeProbe, make_envelope
from tests.support_opencode_binary import worker_binary


def _adapter(**kwargs: Any) -> OpenCodeAdapter:
    kwargs.setdefault("process", FakeProbe())
    return OpenCodeAdapter(**kwargs)


def _preamble(workdir: str, *, stdout_path: str = "") -> WorkerPreamble:
    return WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=workdir)],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
        stdout_path=stdout_path,
    )


# --------------------------------------------------------------------------- #
# The one command builder (D5): every non-interactive kind carries `--format json`.


@pytest.mark.unit
def test_fresh_mint_carries_model_and_format_json_and_auto() -> None:
    cmd = OpenCodeCommand("opencode").build(
        OpenCodeInvocationKind.FRESH, prompt="do the thing", model="openai/gpt-5.6", variant="max", auto=True
    )
    assert cmd == ["opencode", "run", "--format", "json", "--model", "openai/gpt-5.6", "--variant", "max", "--auto", "do the thing"]


@pytest.mark.unit
def test_resume_omits_model_and_carries_session() -> None:
    cmd = OpenCodeCommand("opencode").build(
        OpenCodeInvocationKind.RESUME, prompt="continue", session_id="ses_1", model="openai/gpt-5.6", auto=True
    )
    assert "--model" not in cmd
    assert cmd[cmd.index("--session") + 1] == "ses_1"


@pytest.mark.unit
def test_judge_and_nudge_compose_the_same_shape_as_resume() -> None:
    builder = OpenCodeCommand("opencode")
    judge_cmd = builder.build(OpenCodeInvocationKind.JUDGE, prompt="assess", session_id="ses_1", variant="max", auto=True)
    nudge_cmd = builder.build(OpenCodeInvocationKind.NUDGE, prompt="continue", session_id="ses_1", variant="max", auto=True)
    answer_cmd = builder.build(OpenCodeInvocationKind.ANSWER, prompt="here's the answer", session_id="ses_1", variant="max", auto=True)
    for cmd, prompt in ((judge_cmd, "assess"), (nudge_cmd, "continue"), (answer_cmd, "here's the answer")):
        assert cmd[:6] == ["opencode", "run", "--format", "json", "--session", "ses_1"]
        assert "--variant" in cmd and cmd[cmd.index("--variant") + 1] == "max"
        assert "--auto" in cmd
        assert cmd[-1] == prompt


@pytest.mark.unit
def test_takeover_argv_has_no_format_json_and_no_auto() -> None:
    argv = OpenCodeCommand("opencode").takeover_argv(session_id="ses_1", model="openai/gpt-5.6", variant="max")
    assert argv == ["opencode", "--session", "ses_1", "--model", "openai/gpt-5.6", "--variant", "max"]
    assert "--format" not in argv
    assert "--auto" not in argv


@pytest.mark.unit
def test_build_rejects_the_interactive_kind() -> None:
    with pytest.raises(ValueError):
        OpenCodeCommand("opencode").build(OpenCodeInvocationKind.TAKEOVER, prompt="x")


# --------------------------------------------------------------------------- #
# Model / effort / compaction resolution.


@pytest.mark.unit
def test_resolve_model_strict_maps_a_configured_tier() -> None:
    adapter = _adapter(model_aliases=(("blizzard:frontier", "openai/gpt-5.6-luna"),))
    assert adapter.resolve_model_strict(["blizzard:frontier"]) == "openai/gpt-5.6-luna"


@pytest.mark.unit
def test_resolve_model_strict_returns_none_for_an_unmapped_tier() -> None:
    # No built-in OpenCode tiers (unlike Claude Code): an unmapped tier resolves to
    # nothing, which is the contract a multi-harness selection reads.
    adapter = _adapter()
    assert adapter.resolve_model_strict(["blizzard:frontier"]) is None


@pytest.mark.unit
def test_resolve_model_falls_back_to_the_adapter_default_when_nothing_resolves() -> None:
    adapter = _adapter(model="openai/gpt-5.6-luna")
    assert adapter.resolve_model(["blizzard:frontier"]) == "openai/gpt-5.6-luna"


@pytest.mark.unit
def test_resolve_model_falls_back_to_empty_string_with_no_configured_default() -> None:
    # Empty is legitimate for OpenCode: it lets OpenCode resolve its own configured
    # default rather than Blizzard inventing one.
    adapter = _adapter()
    assert adapter.resolve_model(["blizzard:frontier"]) == ""


@pytest.mark.unit
def test_resolve_model_accepts_a_bare_valid_provider_model_reference() -> None:
    adapter = _adapter()
    assert adapter.resolve_model_strict(["openai/gpt-5.6-luna"]) == "openai/gpt-5.6-luna"


@pytest.mark.unit
def test_resolve_model_skips_a_malformed_native_reference() -> None:
    adapter = _adapter(model_aliases=(("blizzard:basic", "openai/gpt-5.6-mini"),))
    # Not `provider/model` shaped (no slash) — this belongs to another harness's own
    # native vocabulary (e.g. Claude Code's bare `opus`), never handed to this CLI.
    assert adapter.resolve_model(["opus", "blizzard:basic"]) == "openai/gpt-5.6-mini"


@pytest.mark.unit
def test_resolve_effort_passes_through_the_well_known_ordinal() -> None:
    adapter = _adapter()
    assert adapter.resolve_effort("max") == "max"


@pytest.mark.unit
def test_resolve_effort_maps_a_configured_alias() -> None:
    adapter = _adapter(effort_aliases=(("high", "xhigh"),))
    assert adapter.resolve_effort("high") == "xhigh"


@pytest.mark.unit
def test_resolve_effort_drops_an_unrecognized_value() -> None:
    adapter = _adapter()
    assert adapter.resolve_effort("ludicrous") is None


@pytest.mark.unit
def test_resolve_effort_of_none_is_none() -> None:
    assert _adapter().resolve_effort(None) is None


@pytest.mark.unit
def test_resolve_compaction_window_is_always_unsupported() -> None:
    # D8: no numeric translation is ever invented, regardless of the value's shape.
    adapter = _adapter()
    assert adapter.resolve_compaction_window("auto") is None
    assert adapter.resolve_compaction_window("200000") is None
    assert adapter.resolve_compaction_window(None) is None


# --------------------------------------------------------------------------- #
# Harness selection (harness-selection spec): an unmapped tier is a skip, not a spawn.


@pytest.mark.unit
def test_harness_selector_skips_opencode_when_its_tier_is_unmapped() -> None:
    adapter = _adapter()  # no model_aliases at all
    registry = HarnessRegistry({OPENCODE_HARNESS_ID: HarnessBinding(adapter=adapter)})
    envelope = make_envelope(
        "ch_1",
        "build",
        node_id="nd_build",
        choices=[("pass", "ok")],
        session_harnesses=[OPENCODE_HARNESS_ID, "some-other-harness"],
        session_model=["blizzard:frontier"],
    )

    selection = HarnessSelector(harnesses=registry).select(envelope.node)

    assert selection == HarnessSelection(
        harness_id=None,
        skipped=(
            SkippedHarness(OPENCODE_HARNESS_ID, "no-authored-tier"),
            SkippedHarness("some-other-harness", "unknown"),
        ),
    )


@pytest.mark.unit
def test_harness_selector_picks_opencode_once_its_tier_is_mapped() -> None:
    adapter = _adapter(model_aliases=(("blizzard:frontier", "openai/gpt-5.6-luna"),))
    registry = HarnessRegistry({OPENCODE_HARNESS_ID: HarnessBinding(adapter=adapter)})
    envelope = make_envelope(
        "ch_1",
        "build",
        node_id="nd_build",
        choices=[("pass", "ok")],
        session_harnesses=[OPENCODE_HARNESS_ID],
        session_model=["blizzard:frontier"],
    )

    selection = HarnessSelector(harnesses=registry).select(envelope.node)

    assert selection == HarnessSelection(harness_id=OPENCODE_HARNESS_ID, skipped=())


# --------------------------------------------------------------------------- #
# `resume_command` (takeover): no `--format json`, no `--auto`, ever.


@pytest.mark.unit
def test_resume_command_is_the_literal_takeover() -> None:
    cmd = _adapter(binary="opencode").resume_command("/ws/e1", "ses-x")
    assert cmd == "cd /ws/e1 && opencode --session ses-x"


@pytest.mark.unit
def test_resume_command_carries_model_and_variant() -> None:
    cmd = _adapter(binary="opencode").resume_command("/ws/e1", "ses-x", model="openai/gpt-5.6", effort="max")
    assert cmd == "cd /ws/e1 && opencode --session ses-x --model openai/gpt-5.6 --variant max"


@pytest.mark.unit
def test_resume_command_is_identical_attended_or_not() -> None:
    adapter = _adapter(binary="opencode")
    assert adapter.resume_command("/ws/e1", "ses-x", attended=True) == adapter.resume_command("/ws/e1", "ses-x")


@pytest.mark.unit
def test_honors_session_hint_is_false() -> None:
    assert _adapter().honors_session_hint() is False


# --------------------------------------------------------------------------- #
# `spawn` requires an injected stdout path (the identity handshake needs somewhere to read).


@pytest.mark.unit
def test_spawn_without_a_stdout_path_raises() -> None:
    adapter = _adapter(binary="opencode")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    with pytest.raises(HarnessSpawnError):
        adapter.spawn(envelope, _preamble("/ws/e1"), session_hint="hint")


# --------------------------------------------------------------------------- #
# Fresh-session handshake (component): identity arrives on the worker's own stdout.


@pytest.mark.component
def test_fresh_spawn_reads_the_minted_session_id_from_stdout(tmp_path: Path) -> None:
    binary = worker_binary(tmp_path, minted_session_id="ses_minted_abc")
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    adapter = _adapter(binary=binary, process=LinuxProcessProbe())
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    pending = adapter.spawn(envelope, _preamble(str(workdir), stdout_path=str(stdout_path)), session_hint="hint")
    handle = pending.await_identity(5.0)

    assert handle.session_id == "ses_minted_abc"
    assert handle.pid > 0
    assert handle.process_start_time
    os.waitpid(handle.pid, 0)


@pytest.mark.component
def test_fresh_spawn_never_passes_the_hint_as_session_id(tmp_path: Path) -> None:
    binary = worker_binary(tmp_path, minted_session_id="ses_self_assigned")
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    adapter = _adapter(binary=binary, process=LinuxProcessProbe())
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    handle = adapter.spawn(
        envelope, _preamble(str(workdir), stdout_path=str(stdout_path)), session_hint="hint-never-honored"
    ).await_identity(5.0)

    assert handle.session_id == "ses_self_assigned"
    assert handle.session_id != "hint-never-honored"
    os.waitpid(handle.pid, 0)


@pytest.mark.component
def test_fresh_spawn_raises_identity_error_on_malformed_first_record(tmp_path: Path) -> None:
    binary = worker_binary(tmp_path, malformed_first_line=True)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    adapter = _adapter(binary=binary, process=LinuxProcessProbe())
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    pending = adapter.spawn(envelope, _preamble(str(workdir), stdout_path=str(stdout_path)), session_hint="hint")

    with pytest.raises(WorkerIdentityError):
        pending.await_identity(5.0)


@pytest.mark.component
def test_fresh_spawn_raises_identity_error_when_the_process_exits_first(tmp_path: Path) -> None:
    binary = worker_binary(tmp_path, exit_before_output=True)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    adapter = _adapter(binary=binary, process=LinuxProcessProbe())
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    pending = adapter.spawn(envelope, _preamble(str(workdir), stdout_path=str(stdout_path)), session_hint="hint")

    with pytest.raises(WorkerIdentityError):
        pending.await_identity(5.0)


@pytest.mark.component
def test_fresh_spawn_raises_identity_error_on_timeout(tmp_path: Path) -> None:
    """A live process that has written nothing yet is a plain timeout, not a crash."""
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    stdout_path.write_bytes(b"")
    adapter = _adapter(binary="opencode", process=FakeProbe(alive={(4242, "start-token")}))
    pending = _PendingOpenCodeIdentity(
        pid=4242, pgid=4242, process_start_time="start-token", stdout_path=str(stdout_path), process=adapter._process
    )

    with pytest.raises(WorkerIdentityError):
        pending.await_identity(0.1)


@pytest.mark.component
def test_resume_spawn_never_performs_the_handshake(tmp_path: Path) -> None:
    """A resume already knows its session id — no polling, an instant `WorkerHandle`."""
    binary = worker_binary(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    adapter = _adapter(binary=binary, process=LinuxProcessProbe())
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    pending = adapter.spawn(
        envelope, _preamble(str(workdir), stdout_path=str(stdout_path)), session_hint=None, resume_from="ses_prior"
    )

    handle = pending.await_identity(0)  # already a `WorkerHandle` — trivial phase two
    assert handle.session_id == "ses_prior"
    os.waitpid(handle.pid, 0)


@pytest.mark.component
def test_judge_and_resume_with_message_launch_against_the_recorded_session(tmp_path: Path) -> None:
    binary = worker_binary(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    adapter = _adapter(binary=binary, process=LinuxProcessProbe())

    judge_handle = adapter.judge(str(workdir), "ses_recorded", "assess", str(workdir / "judge-output.json"))
    os.waitpid(judge_handle.pid, 0)
    output = Path(workdir / "judge-output.json").read_text()
    assert adapter.parse_verdict(output) == "pass"

    pid = adapter.resume_with_message(str(workdir), "ses_recorded", "continue", stdout_path=str(workdir / "nudge.out"))
    os.waitpid(pid, 0)
    assert (workdir / "nudge.out").exists()


@pytest.mark.component
def test_spawn_launches_through_the_process_launcher_with_its_own_group(tmp_path: Path) -> None:
    binary = worker_binary(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    adapter = _adapter(binary=binary, process=LinuxProcessProbe())
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    handle = adapter.spawn(
        envelope, _preamble(str(workdir), stdout_path=str(stdout_path)), session_hint="hint"
    ).await_identity(5.0)

    assert handle.pgid is not None
    assert handle.pgid == handle.pid  # `start_new_session=True`'s own POSIX contract (D3)
    os.waitpid(handle.pid, 0)


# --------------------------------------------------------------------------- #
# Model pinned at fresh mint only (component-level argv proof via a captured Popen).


def _fake_popen_capturing(captured: dict[str, list[str]]) -> object:
    class _FakeSpawnedProcess:
        pid = 9_999_999

    def _fake_popen(cmd: list[str], **kwargs: object) -> _FakeSpawnedProcess:
        captured["cmd"] = cmd
        return _FakeSpawnedProcess()

    return _fake_popen


@pytest.mark.unit
def test_spawn_pins_the_resolved_model_at_mint_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    adapter = _adapter(binary="opencode")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    adapter.spawn(
        envelope,
        _preamble(str(workdir), stdout_path=str(stdout_path)),
        session_hint="hint",
        model="openai/gpt-5.6-luna",
    )

    cmd = captured["cmd"]
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "openai/gpt-5.6-luna"
    assert "--session" not in cmd


@pytest.mark.unit
def test_spawn_with_resume_from_omits_model_and_carries_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    adapter = _adapter(binary="opencode")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    handle = adapter.spawn(
        envelope,
        _preamble(str(workdir), stdout_path=str(stdout_path)),
        session_hint=None,
        resume_from="ses_prior",
        model="openai/gpt-5.6-luna",
    )

    cmd = captured["cmd"]
    assert "--model" not in cmd
    assert cmd[cmd.index("--session") + 1] == "ses_prior"
    assert handle.await_identity(0).session_id == "ses_prior"
