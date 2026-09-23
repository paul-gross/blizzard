"""The OpenCode adapter — command composition (unit) and a real subprocess (component).

Mirrors ``tests/test_runner_harness_adapter.py``'s split: unit tests drive command building
and model/effort/compaction resolution against a monkeypatched ``subprocess.Popen``; component
tests launch a real fake ``opencode`` binary (``tests/support_opencode_binary.py``)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import HarnessSpawnError, ResumeHandle, WorkerIdentityError, WorkerPreamble
from blizzard.runner.harness.identity import OPENCODE_HARNESS_ID
from blizzard.runner.harness.internal.offline_compatibility import admitted_corpus_versions
from blizzard.runner.harness.internal.opencode_adapter import (
    _MAX_IDENTITY_PREAMBLE_LINES,
    OpenCodeAdapter,
    _PendingOpenCodeIdentity,
)
from blizzard.runner.harness.internal.opencode_command import OpenCodeCommand, OpenCodeInvocationKind
from blizzard.runner.harness.internal.opencode_price_cache import (
    FileOpenCodePriceCatalog,
    IOpenCodePriceCatalog,
    OpenCodeContextTier,
    OpenCodeModelPrice,
    OpenCodeRate,
)
from blizzard.runner.harness.internal.opencode_probe import ADMITTED_OPENCODE_RANGE
from blizzard.runner.harness.process_launch import ProcessLauncher
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.loop.process import LinuxProcessProbe
from blizzard.runner.loop.session import HarnessSelection, HarnessSelector, SkippedHarness
from tests.opencode_usage_limit_fixture import USAGE_LIMIT_EVENT
from tests.runner_fakes import FakeProbe, make_envelope
from tests.support_opencode_binary import worker_binary

# The oldest committed corpus inside the admitted range (blizzard#438) — stays correct as corpora are added.
_AN_ADMITTED_OPENCODE_VERSION = admitted_corpus_versions("opencode", ADMITTED_OPENCODE_RANGE)[0]
_CORPUS_DIR = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "blizzard"
    / "runner"
    / "harness"
    / "contracts"
    / "opencode"
    / _AN_ADMITTED_OPENCODE_VERSION
)


def _manifest() -> dict[str, Any]:
    return json.loads((_CORPUS_DIR / "manifest.json").read_text())


def _fixtures() -> list[tuple[str, dict[str, Any]]]:
    manifest = _manifest()
    return [(entry["name"], json.loads((_CORPUS_DIR / entry["path"]).read_text())) for entry in manifest["fixtures"]]


def _fixture(name: str) -> dict[str, Any]:
    return dict(_fixtures())[name]


def _jsonl(events: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(event) for event in events)


@pytest.fixture(autouse=True)
def _opencode_resolves_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirrors ``test_runner_harness_adapter.py``'s fixture of the same shape: a unit
    test's fake spawn must not depend on whether ``opencode`` is really installed on this
    machine's ``PATH`` — `_ensure_executable`'s `shutil.which` lookup (F1) runs before the
    faked ``subprocess.Popen`` ever sees the call."""
    monkeypatch.setattr(shutil, "which", lambda binary, path=None: f"/usr/bin/{binary}")


def _adapter(**kwargs: Any) -> OpenCodeAdapter:
    process = kwargs.setdefault("process", FakeProbe())
    kwargs.setdefault("launcher", ProcessLauncher(process))
    return OpenCodeAdapter(**kwargs)


def _preamble(workdir: str, *, stdout_path: str = "", stderr_path: str = "") -> WorkerPreamble:
    return WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=workdir)],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )


# --------------------------------------------------------------------------- #
# The one command builder (D5): every non-interactive kind carries `--format json`.


@pytest.mark.unit
def test_fresh_mint_carries_model_and_format_json_and_auto() -> None:
    cmd = OpenCodeCommand("opencode").build(
        OpenCodeInvocationKind.FRESH, prompt="do the thing", model="openai/gpt-5.6", variant="max", auto=True
    )
    assert cmd == [
        "opencode",
        "run",
        "--format",
        "json",
        "--model",
        "openai/gpt-5.6",
        "--variant",
        "max",
        "--auto",
        "do the thing",
    ]


@pytest.mark.unit
def test_resume_omits_model_and_carries_session() -> None:
    cmd = OpenCodeCommand("opencode").build(
        OpenCodeInvocationKind.RESUME, prompt="continue", session_id="ses_1", model="openai/gpt-5.6", auto=True
    )
    assert "--model" not in cmd
    assert cmd[cmd.index("--session") + 1] == "ses_1"


@pytest.mark.unit
def test_judge_and_nudge_compose_the_same_shape_as_resume() -> None:
    """NUDGE serves both a produces-nudge and a parked-answer delivery (review F14): the two
    are distinct CALLER intents with no OpenCode CLI-level difference, so there is no
    separate ANSWER kind to also exercise here — NUDGE already covers both."""
    builder = OpenCodeCommand("opencode")
    judge_cmd = builder.build(
        OpenCodeInvocationKind.JUDGE, prompt="assess", session_id="ses_1", variant="max", auto=True
    )
    nudge_cmd = builder.build(
        OpenCodeInvocationKind.NUDGE, prompt="continue", session_id="ses_1", variant="max", auto=True
    )
    for cmd, prompt in ((judge_cmd, "assess"), (nudge_cmd, "continue")):
        assert cmd[:6] == ["opencode", "run", "--format", "json", "--session", "ses_1"]
        assert "--variant" in cmd and cmd[cmd.index("--variant") + 1] == "max"
        assert "--auto" in cmd
        assert cmd[-1] == prompt


@pytest.mark.unit
def test_takeover_argv_has_no_format_json_and_no_auto() -> None:
    """Interactive takeover has no kind on `OpenCodeInvocationKind` at all (review F14) — it
    is composed by this wholly separate method, never through `OpenCodeCommand.build`."""
    argv = OpenCodeCommand("opencode").takeover_argv(session_id="ses_1", model="openai/gpt-5.6", variant="max")
    assert argv == ["opencode", "--session", "ses_1", "--model", "openai/gpt-5.6", "--variant", "max"]
    assert "--format" not in argv
    assert "--auto" not in argv


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
# `resolvable_tier_ids` (blizzard#433): the capability snapshot's own tier enumeration.


@pytest.mark.unit
def test_resolvable_tier_ids_is_empty_with_no_config_at_all() -> None:
    # No built-in OpenCode tiers (unlike Claude Code's three): with nothing configured,
    # nothing is resolvable.
    adapter = _adapter()
    assert adapter.resolvable_tier_ids() == ()


@pytest.mark.unit
def test_resolvable_tier_ids_names_a_configured_alias() -> None:
    adapter = _adapter(model_aliases=(("blizzard:frontier", "openai/gpt-5.6-luna"),))
    assert set(adapter.resolvable_tier_ids()) == {"blizzard:frontier"}


@pytest.mark.unit
def test_resolvable_tier_ids_merges_every_configured_alias_once() -> None:
    # Override-by-key precedence, matching `_resolve_one_model`: two distinct aliases
    # both appear, each exactly once.
    adapter = _adapter(
        model_aliases=(
            ("blizzard:frontier", "openai/gpt-5.6-luna"),
            ("blizzard:basic", "openai/gpt-5.6-mini"),
        )
    )
    tiers = adapter.resolvable_tier_ids()
    assert tiers.count("blizzard:frontier") == 1
    assert set(tiers) == {"blizzard:frontier", "blizzard:basic"}


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
    pending.confirm_durable()  # F1: real component tests stand in for `Spawner.spawn`'s own call
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

    pending = adapter.spawn(
        envelope, _preamble(str(workdir), stdout_path=str(stdout_path)), session_hint="hint-never-honored"
    )
    pending.confirm_durable()  # F1: real component tests stand in for `Spawner.spawn`'s own call
    handle = pending.await_identity(5.0)

    assert handle.session_id == "ses_self_assigned"
    assert handle.session_id != "hint-never-honored"
    os.waitpid(handle.pid, 0)


@pytest.mark.component
def test_fresh_spawn_writes_stderr_to_the_injected_path(tmp_path: Path) -> None:
    """``spawn`` honors ``preamble.stderr_path`` the same way Claude Code's binding does —
    every OpenCode failure event otherwise reports an empty stderr tail even though the
    runner already allocated the file (blizzard#433)."""
    binary = worker_binary(tmp_path, minted_session_id="ses_minted_abc", stderr_message="diagnostic-sentinel")
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    stderr_path = tmp_path / "lease-1.stderr"
    adapter = _adapter(binary=binary, process=LinuxProcessProbe())
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    pending = adapter.spawn(
        envelope,
        _preamble(str(workdir), stdout_path=str(stdout_path), stderr_path=str(stderr_path)),
        session_hint="hint",
    )
    pending.confirm_durable()  # F1: real component tests stand in for `Spawner.spawn`'s own call
    handle = pending.await_identity(5.0)
    os.waitpid(handle.pid, 0)

    assert "diagnostic-sentinel" in stderr_path.read_text()


@pytest.mark.component
def test_fresh_spawn_raises_identity_error_on_malformed_first_record(tmp_path: Path) -> None:
    binary = worker_binary(tmp_path, malformed_first_line=True)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    adapter = _adapter(binary=binary, process=LinuxProcessProbe())
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    pending = adapter.spawn(envelope, _preamble(str(workdir), stdout_path=str(stdout_path)), session_hint="hint")
    pending.confirm_durable()  # F1: real component tests stand in for `Spawner.spawn`'s own call

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
    pending.confirm_durable()  # F1: real component tests stand in for `Spawner.spawn`'s own call

    with pytest.raises(WorkerIdentityError):
        pending.await_identity(5.0)


@pytest.mark.component
def test_fresh_spawn_identity_error_carries_the_workers_own_stderr(tmp_path: Path) -> None:
    """A dead-before-identity worker's real cause lives only in its own stderr capture —
    surfaced on the raised error, since it is otherwise lost the moment the lease is marked
    identity-failed and the group is killed."""
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    stdout_path.write_bytes(b"")
    stderr_path = tmp_path / "lease-1.stderr"
    stderr_path.write_text("Traceback (most recent call last):\nRuntimeError: mock-opencode blew up\n")
    adapter = _adapter(binary="opencode", process=FakeProbe(alive=set()))  # already gone
    pending = _PendingOpenCodeIdentity(
        pid=4242,
        pgid=4242,
        process_start_time="start-token",
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        process=adapter._process,
    )

    with pytest.raises(WorkerIdentityError, match="mock-opencode blew up"):
        pending.await_identity(0.1)


@pytest.mark.component
def test_fresh_spawn_raises_identity_error_on_timeout(tmp_path: Path) -> None:
    """A live process that has written nothing yet is a plain timeout, not a crash."""
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    stdout_path.write_bytes(b"")
    adapter = _adapter(binary="opencode", process=FakeProbe(alive={(4242, "start-token")}))
    pending = _PendingOpenCodeIdentity(
        pid=4242,
        pgid=4242,
        process_start_time="start-token",
        stdout_path=str(stdout_path),
        stderr_path="",
        process=adapter._process,
    )

    with pytest.raises(WorkerIdentityError):
        pending.await_identity(0.1)


@pytest.mark.component
def test_fresh_spawn_succeeds_when_the_process_already_exited_after_flushing_identity(tmp_path: Path) -> None:
    """F7: a worker that flushed its identity record then exited fast is a SUCCESS — the
    valid record is checked before liveness, not after, so this must not raise even though
    the process is already dead by the time ``await_identity`` looks."""
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    stdout_path.write_bytes(
        json.dumps(
            {
                "type": "step_start",
                "sessionID": "ses_flushed",
                "part": {"id": "prt_start", "sessionID": "ses_flushed", "messageID": "msg_1", "type": "step-start"},
            }
        ).encode()
        + b"\n"
    )
    adapter = _adapter(binary="opencode", process=FakeProbe(alive=set()))  # already gone
    pending = _PendingOpenCodeIdentity(
        pid=4242,
        pgid=4242,
        process_start_time="start-token",
        stdout_path=str(stdout_path),
        stderr_path="",
        process=adapter._process,
    )

    handle = pending.await_identity(0.1)

    assert handle.session_id == "ses_flushed"


@pytest.mark.component
def test_first_event_tolerates_leading_non_json_lines(tmp_path: Path) -> None:
    """Identity arrives on the worker's own SHARED stdout — an earlier writer (a tool
    banner, a stray line) may put non-JSON ahead of the real first record. Byte zero need
    not be it: the handshake skips leading noise and finds identity further down."""
    stdout_path = tmp_path / "lease-1.stdout"
    identity_line = json.dumps(
        {
            "type": "step_start",
            "sessionID": "ses_after_banner",
            "part": {"id": "prt_start", "sessionID": "ses_after_banner", "messageID": "msg_1", "type": "step-start"},
        }
    )
    stdout_path.write_text(f"A tool banner opencode never asked for\nnot json either\n{identity_line}\n")
    adapter = _adapter(binary="opencode", process=FakeProbe(alive={(4242, "start-token")}))
    pending = _PendingOpenCodeIdentity(
        pid=4242,
        pgid=4242,
        process_start_time="start-token",
        stdout_path=str(stdout_path),
        stderr_path="",
        process=adapter._process,
    )

    handle = pending.await_identity(1.0)

    assert handle.session_id == "ses_after_banner"


@pytest.mark.component
def test_first_event_raises_once_the_leading_noise_bound_is_exceeded(tmp_path: Path) -> None:
    """The leading-noise tolerance is bounded: a permanently noisy stream with no valid
    identity in the first ``_MAX_IDENTITY_PREAMBLE_LINES`` lines fails the handshake
    outright, rather than waiting on ``await_identity``'s own timeout."""
    stdout_path = tmp_path / "lease-1.stdout"
    stdout_path.write_text("not json\n" * (_MAX_IDENTITY_PREAMBLE_LINES + 1))
    adapter = _adapter(binary="opencode", process=FakeProbe(alive={(4242, "start-token")}))
    pending = _PendingOpenCodeIdentity(
        pid=4242,
        pgid=4242,
        process_start_time="start-token",
        stdout_path=str(stdout_path),
        stderr_path="",
        process=adapter._process,
    )

    with pytest.raises(WorkerIdentityError):
        pending.await_identity(1.0)


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
    pending.confirm_durable()  # F1: real component tests stand in for `Spawner.spawn`'s own call

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
    judge_handle.confirm_durable()  # F1: real component tests stand in for `Judgement._elicit`'s own call
    os.waitpid(judge_handle.pid, 0)
    output = Path(workdir / "judge-output.json").read_text()
    assert adapter.parse_verdict(output) == "pass"

    resumed = adapter.resume_with_message(
        str(workdir), "ses_recorded", "continue", stdout_path=str(workdir / "nudge.out")
    )
    resumed.confirm_durable()  # F1: real component tests stand in for `dormant.py::_wake`'s own call
    os.waitpid(resumed.pid, 0)
    assert (workdir / "nudge.out").exists()


@pytest.mark.component
def test_spawn_launches_through_the_process_launcher_with_its_own_group(tmp_path: Path) -> None:
    binary = worker_binary(tmp_path)
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    adapter = _adapter(binary=binary, process=LinuxProcessProbe())
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    pending = adapter.spawn(envelope, _preamble(str(workdir), stdout_path=str(stdout_path)), session_hint="hint")
    pending.confirm_durable()  # F1: real component tests stand in for `Spawner.spawn`'s own call
    handle = pending.await_identity(5.0)

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


@pytest.mark.unit
def test_resume_with_message_stamps_process_start_time_and_a_real_confirm_durable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F4: a resume gets the same D1/D4 ownership a fresh spawn or judge gets — the
    launcher's own recorded start time (D3), and a real disarm signal, not
    `ResumeHandle`'s bare no-op default."""
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    probe = FakeProbe(alive={(9_999_999, "fake-resume-start-time")})
    adapter = OpenCodeAdapter(binary="opencode", process=probe, launcher=ProcessLauncher(probe))

    resumed = adapter.resume_with_message("/ws", "ses_recorded", "continue")

    assert resumed.pid == 9_999_999
    assert resumed.process_start_time == "fake-resume-start-time"
    assert resumed.confirm_durable is not ResumeHandle.__dataclass_fields__["confirm_durable"].default


# --------------------------------------------------------------------------- #
# Output and usage (execution spec, "Output and usage") — driven off the full pinned corpus.

_EXPECTED_VERDICT_AND_ASSESSMENT: dict[str, tuple[str | None, str]] = {
    "success": ("pass", ""),
    "provider_error": (None, "ProviderError: provider request failed (status 503)"),
    "permission_denial": (None, ""),
    "interrupted_tool": (None, ""),
    "compaction": ("pass", ""),
    "child_session": (None, ""),
    "live_success": ("pass", "<Choice>pass</Choice>"),
}

_EXPECTED_USABLE: dict[str, bool] = {
    "success": True,
    "provider_error": False,
    "permission_denial": True,
    "interrupted_tool": False,
    "compaction": True,
    "child_session": True,
    "live_success": True,
}

# (input, output — reasoning folded in, cache_read, cache_create, cost_usd)
_EXPECTED_USAGE: dict[str, tuple[int, int, int, int, float | None]] = {
    "success": (120, 45 + 18, 30, 15, 0.0123),
    "provider_error": None,  # type: ignore[dict-item]  # no completed step at all
    "permission_denial": (40, 8, 0, 0, None),  # cost 0 reads as unknown, never free
    "interrupted_tool": None,  # type: ignore[dict-item]
    "compaction": (200, 25 + 10, 90, 5, None),
    "child_session": (80, 20 + 4, 10, 2, None),
    "live_success": (120 + 30, (45 + 18) + (8 + 0), 30 + 20, 15 + 0, None),  # two completed steps, one turn
}


@pytest.mark.unit
@pytest.mark.parametrize("name,payload", _fixtures(), ids=lambda item: item if isinstance(item, str) else "fixture")
def test_verdict_and_assessment_match_the_expected_shape_for_every_fixture(name: str, payload: dict[str, Any]) -> None:
    output = _jsonl(payload["events"])
    adapter = _adapter()

    expected_verdict, expected_assessment = _EXPECTED_VERDICT_AND_ASSESSMENT[name]
    assert adapter.parse_verdict(output) == expected_verdict
    assert adapter.parse_assessment(output) == expected_assessment


@pytest.mark.unit
@pytest.mark.parametrize("name,payload", _fixtures(), ids=lambda item: item if isinstance(item, str) else "fixture")
def test_has_usable_output_matches_the_expected_shape_for_every_fixture(name: str, payload: dict[str, Any]) -> None:
    output = _jsonl(payload["events"])
    assert _adapter().has_usable_output(output) is _EXPECTED_USABLE[name]


@pytest.mark.unit
@pytest.mark.parametrize("name,payload", _fixtures(), ids=lambda item: item if isinstance(item, str) else "fixture")
def test_parse_usage_matches_the_expected_shape_for_every_fixture(name: str, payload: dict[str, Any]) -> None:
    output = _jsonl(payload["events"])
    sample = _adapter().parse_usage(output, "spawn")

    expected = _EXPECTED_USAGE[name]
    if expected is None:
        assert sample is None
        return
    input_tokens, output_tokens, cache_read_tokens, cache_create_tokens, cost_usd = expected
    assert sample is not None
    assert (sample.input_tokens, sample.output_tokens, sample.cache_read_tokens, sample.cache_create_tokens) == (
        input_tokens,
        output_tokens,
        cache_read_tokens,
        cache_create_tokens,
    )
    assert sample.cost_usd == cost_usd


@pytest.mark.unit
def test_verdict_body_never_carries_tool_output_or_child_session_text() -> None:
    # `child_session`'s tool call and its child's conversation must never leak into the
    # root turn's verdict body — the corpus's sharpest exclusion case.
    payload = _fixture("child_session")
    output = _jsonl(payload["events"])
    adapter = _adapter()

    assert adapter.parse_verdict(output) is None
    assert adapter.parse_assessment(output) == ""
    # The child's own reply lives only in `child_export`, a document this adapter never
    # reads for verdict/assessment parsing — asserted structurally below.

    for fixture_name, fixture_payload in _fixtures():
        fixture_output = _jsonl(fixture_payload["events"])
        for event in fixture_payload["events"]:
            part = event.get("part") or {}
            tool_output = (part.get("state") or {}).get("output")
            if tool_output:
                verdict = adapter.parse_verdict(fixture_output) or ""
                assessment = adapter.parse_assessment(fixture_output)
                assert tool_output not in verdict, fixture_name
                assert tool_output not in assessment, fixture_name


@pytest.mark.unit
def test_parse_usage_and_has_usable_output_tolerate_malformed_capture() -> None:
    adapter = _adapter()
    assert adapter.parse_verdict("not json at all") is None
    assert adapter.parse_assessment("not json at all") == ""
    assert adapter.has_usable_output("not json at all") is False
    assert adapter.parse_usage("not json at all", "spawn") is None


@pytest.mark.unit
def test_parse_events_skips_one_malformed_trailing_line_and_keeps_the_rest() -> None:
    """A killed-mid-write worker can leave one truncated line behind an otherwise-complete
    capture; OpenCode has no transcript fallback to re-derive a lost verdict from, so
    skipping just that one bad line is the entire tolerance this binding can offer."""
    payload = _fixture("success")
    output = _jsonl(payload["events"]) + "\nnot json at all, and truncated besides"
    adapter = _adapter()

    assert adapter.parse_verdict(output) == "pass"
    assert adapter.has_usable_output(output) is True
    sample = adapter.parse_usage(output, "spawn")
    assert sample is not None
    assert (sample.input_tokens, sample.output_tokens, sample.cache_read_tokens, sample.cache_create_tokens) == (
        120,
        45 + 18,
        30,
        15,
    )
    assert sample.cost_usd == 0.0123


@pytest.mark.unit
def test_sum_transcript_usage_dedups_a_step_described_by_both_event_and_exported_message() -> None:
    # `child_session` exercises this: the root's one completed step ("prt_child_finish")
    # is described both by the process's stdout event and the exported message.
    payload = _fixture("child_session")
    event_lines = [json.dumps(event) for event in payload["events"]]
    message_lines = [json.dumps(message) for message in payload["export"]["messages"]]
    adapter = _adapter()

    events_only = adapter.sum_transcript_usage(event_lines, "spawn")
    mixed = adapter.sum_transcript_usage(event_lines + message_lines, "spawn")

    expected = (80, 20 + 4, 10, 2)
    assert (
        events_only.input_tokens,
        events_only.output_tokens,
        events_only.cache_read_tokens,
        events_only.cache_create_tokens,
    ) == expected
    assert (mixed.input_tokens, mixed.output_tokens, mixed.cache_read_tokens, mixed.cache_create_tokens) == expected
    # A transcript never carries a dollar figure, regardless of what the events themselves reported.
    assert mixed.cost_usd is None


@pytest.mark.unit
def test_sum_transcript_usage_skips_tokenless_user_messages_without_raising() -> None:
    payload = _fixture("success")
    lines = [json.dumps(message) for message in payload["export"]["messages"]]  # includes the user message

    sample = _adapter().sum_transcript_usage(lines, "spawn")

    assert (sample.input_tokens, sample.output_tokens, sample.cache_read_tokens, sample.cache_create_tokens) == (
        120,
        45 + 18,
        30,
        15,
    )
    assert sample.cost_usd is None


@pytest.mark.unit
def test_sum_transcript_usage_recovers_step_finish_from_a_message_with_an_empty_tool_title() -> None:
    # A tool that matched nothing reports `"title": ""`; that part must not cost its message's usage.
    message = _fixture("success")["export"]["messages"][1]
    message["parts"] = [part for part in message["parts"] if part["id"] in ("prt_success_glob", "prt_success_finish")]
    assert [part["type"] for part in message["parts"]] == ["tool", "step-finish"]
    lines = [json.dumps(message)]

    sample = _adapter().sum_transcript_usage(lines, "spawn")

    assert (sample.input_tokens, sample.output_tokens, sample.cache_read_tokens, sample.cache_create_tokens) == (
        120,
        45 + 18,
        30,
        15,
    )
    assert sample.cost_usd is None


@pytest.mark.unit
def test_sum_transcript_usage_ignores_unparseable_lines() -> None:
    sample = _adapter().sum_transcript_usage(
        ["", "not json", "{}", '{"type": "future_event", "sessionID": "x"}'], "spawn"
    )

    assert (sample.input_tokens, sample.output_tokens, sample.cache_read_tokens, sample.cache_create_tokens) == (
        0,
        0,
        0,
        0,
    )
    assert sample.cost_usd is None


# --- OpenCode price estimation ----------------------------------------------

# `gpt-5.6-luna`'s base rates (the issue's worked example), dollars per 1,000,000 tokens.
_LUNA_RATE = OpenCodeRate(input=0.20, output=1.20, cache_read=0.02, cache_write=0.25)
_LUNA_TIER = OpenCodeContextTier(
    size=100, rate=OpenCodeRate(input=0.40, output=1.80, cache_read=0.04, cache_write=0.50)
)


@dataclass(frozen=True)
class _FakePriceCatalog:
    """A price catalog pinned to exactly the entries a test names — never file I/O."""

    prices: dict[tuple[str, str], OpenCodeModelPrice]

    def price_for(self, provider: str, model: str) -> OpenCodeModelPrice | None:
        return self.prices.get((provider, model))


def _conforms_fake_catalog(x: _FakePriceCatalog) -> IOpenCodePriceCatalog:
    return x


def _luna_catalog(**price_kwargs: Any) -> _FakePriceCatalog:
    return _FakePriceCatalog({("openai", "gpt-5.6-luna"): OpenCodeModelPrice(base=_LUNA_RATE, **price_kwargs)})


def _step_finish_event(
    *, session_id: str, part_id: str, message_id: str, cost: float, tokens: dict[str, Any]
) -> dict[str, Any]:
    return {
        "type": "step_finish",
        "sessionID": session_id,
        "part": {
            "id": part_id,
            "sessionID": session_id,
            "messageID": message_id,
            "type": "step-finish",
            "reason": "stop",
            "cost": cost,
            "tokens": tokens,
        },
    }


def _export_message(
    *,
    session_id: str,
    message_id: str,
    provider_id: str,
    model_id: str,
    part_id: str,
    cost: float,
    tokens: dict[str, Any],
) -> dict[str, Any]:
    return {
        "info": {
            "id": message_id,
            "sessionID": session_id,
            "role": "assistant",
            "providerID": provider_id,
            "modelID": model_id,
        },
        "parts": [
            {
                "id": part_id,
                "sessionID": session_id,
                "messageID": message_id,
                "type": "step-finish",
                "reason": "stop",
                "cost": cost,
                "tokens": tokens,
            }
        ],
    }


_ZERO_TOKENS = {"input": 40, "output": 8, "reasoning": 0, "cache": {"read": 0, "write": 0}}


@pytest.mark.unit
def test_parse_usage_estimates_an_all_zero_cost_step_when_its_model_is_priced() -> None:
    payload = _fixture("permission_denial")
    output = _jsonl(payload["events"])
    adapter = _adapter(model="openai/gpt-5.6-luna", price_catalog=_luna_catalog())

    sample = adapter.parse_usage(output, "spawn")

    assert sample is not None
    assert sample.cost_usd is None
    assert sample.estimated_cost_usd == pytest.approx((40 * 0.20 + 8 * 1.20) / 1_000_000)


@pytest.mark.unit
def test_parse_usage_leaves_a_non_zero_cost_step_billed_with_no_estimate() -> None:
    payload = _fixture("success")
    output = _jsonl(payload["events"])
    adapter = _adapter(model="openai/gpt-5.6-luna", price_catalog=_luna_catalog())

    sample = adapter.parse_usage(output, "spawn")

    assert sample is not None
    assert sample.cost_usd == pytest.approx(0.0123)
    assert sample.estimated_cost_usd is None


@pytest.mark.unit
def test_parse_usage_reports_both_a_billed_and_an_estimated_amount_for_a_mixed_invocation() -> None:
    events = [
        _step_finish_event(
            session_id="ses_mixed",
            part_id="prt_billed",
            message_id="msg_billed",
            cost=0.05,
            tokens={"input": 1_000, "output": 200, "reasoning": 0, "cache": {"read": 0, "write": 0}},
        ),
        _step_finish_event(
            session_id="ses_mixed", part_id="prt_zero", message_id="msg_zero", cost=0.0, tokens=_ZERO_TOKENS
        ),
    ]
    adapter = _adapter(model="openai/gpt-5.6-luna", price_catalog=_luna_catalog())

    sample = adapter.parse_usage(_jsonl(events), "spawn")

    assert sample is not None
    assert sample.cost_usd == pytest.approx(0.05)
    assert sample.estimated_cost_usd == pytest.approx((40 * 0.20 + 8 * 1.20) / 1_000_000)


@pytest.mark.unit
def test_parse_usage_prices_two_steps_on_either_side_of_a_tier_threshold_at_their_own_rate() -> None:
    # F = input + cache.read + cache.write: 40 stays under the 100 tier, 150 clears it.
    below = {"input": 40, "output": 10, "reasoning": 0, "cache": {"read": 0, "write": 0}}
    above = {"input": 150, "output": 10, "reasoning": 0, "cache": {"read": 0, "write": 0}}
    events = [
        _step_finish_event(session_id="ses_tier", part_id="prt_below", message_id="msg_below", cost=0.0, tokens=below),
        _step_finish_event(session_id="ses_tier", part_id="prt_above", message_id="msg_above", cost=0.0, tokens=above),
    ]
    adapter = _adapter(model="openai/gpt-5.6-luna", price_catalog=_luna_catalog(tiers=(_LUNA_TIER,)))

    sample = adapter.parse_usage(_jsonl(events), "spawn")

    below_expected = (40 * 0.20 + 10 * 1.20) / 1_000_000
    above_expected = (150 * 0.40 + 10 * 1.80) / 1_000_000
    assert sample is not None
    assert sample.estimated_cost_usd == pytest.approx(below_expected + above_expected)
    assert below_expected != above_expected  # each step really did price at its own rate


@pytest.mark.unit
def test_parse_usage_gives_no_estimate_when_the_invocation_model_does_not_parse() -> None:
    payload = _fixture("permission_denial")
    output = _jsonl(payload["events"])
    # No slash: not a valid `provider/model` reference, so the fallback label is unknown.
    adapter = _adapter(model="not-a-valid-model-reference", price_catalog=_luna_catalog())

    sample = adapter.parse_usage(output, "spawn")

    assert sample is not None
    assert sample.estimated_cost_usd is None


@pytest.mark.unit
def test_parse_usage_gives_no_estimate_with_no_catalog_injected() -> None:
    payload = _fixture("permission_denial")
    output = _jsonl(payload["events"])
    adapter = _adapter(model="openai/gpt-5.6-luna")  # no price_catalog kwarg at all

    sample = adapter.parse_usage(output, "spawn")

    assert sample is not None
    assert sample.estimated_cost_usd is None


@pytest.mark.unit
def test_parse_usage_leaves_a_zero_cost_step_unknown_when_the_cache_file_is_missing(tmp_path: Path) -> None:
    payload = _fixture("permission_denial")
    output = _jsonl(payload["events"])
    catalog = FileOpenCodePriceCatalog(tmp_path / "does-not-exist" / "models.json")
    adapter = _adapter(model="openai/gpt-5.6-luna", price_catalog=catalog)

    sample = adapter.parse_usage(output, "spawn")

    assert sample is not None
    assert sample.cost_usd is None
    assert sample.estimated_cost_usd is None


@pytest.mark.unit
def test_sum_transcript_usage_estimates_a_zero_cost_step_from_its_own_export_message_model() -> None:
    # The invocation's own model resolves to a provider the catalog has no entry for — the
    # estimate can only have come from the exported message's own providerID/modelID.
    line = json.dumps(
        _export_message(
            session_id="ses_export",
            message_id="msg_export",
            provider_id="openai",
            model_id="gpt-5.6-luna",
            part_id="prt_export",
            cost=0.0,
            tokens=_ZERO_TOKENS,
        )
    )
    adapter = _adapter(model="anthropic/claude-unpriced", price_catalog=_luna_catalog())

    sample = adapter.sum_transcript_usage([line], "spawn")

    assert sample.cost_usd is None
    assert sample.estimated_cost_usd == pytest.approx((40 * 0.20 + 8 * 1.20) / 1_000_000)


@pytest.mark.unit
def test_sum_transcript_usage_never_bills_but_may_still_carry_an_estimate() -> None:
    line = json.dumps(
        _export_message(
            session_id="ses_export",
            message_id="msg_export",
            provider_id="openai",
            model_id="gpt-5.6-luna",
            part_id="prt_export",
            cost=0.0,
            tokens=_ZERO_TOKENS,
        )
    )
    adapter = _adapter(model="openai/gpt-5.6-luna", price_catalog=_luna_catalog())

    sample = adapter.sum_transcript_usage([line], "spawn")

    assert sample.cost_usd is None
    assert sample.estimated_cost_usd is not None


@pytest.mark.unit
def test_sum_transcript_usage_drops_the_estimate_entirely_when_any_step_reported_a_real_cost() -> None:
    """A dropped billed amount is never hidden behind an estimate: once one step's
    real cost is unrecoverable on this fallback path, the whole sample stays without one,
    even though another step in the same transcript was priceable."""
    billed_line = json.dumps(
        _export_message(
            session_id="ses_export",
            message_id="msg_billed",
            provider_id="openai",
            model_id="gpt-5.6-luna",
            part_id="prt_billed",
            cost=0.05,
            tokens={"input": 1_000, "output": 200, "reasoning": 0, "cache": {"read": 0, "write": 0}},
        )
    )
    zero_line = json.dumps(
        _export_message(
            session_id="ses_export",
            message_id="msg_zero",
            provider_id="openai",
            model_id="gpt-5.6-luna",
            part_id="prt_zero",
            cost=0.0,
            tokens=_ZERO_TOKENS,
        )
    )
    adapter = _adapter(model="openai/gpt-5.6-luna", price_catalog=_luna_catalog())

    sample = adapter.sum_transcript_usage([billed_line, zero_line], "spawn")

    assert sample.cost_usd is None
    assert sample.estimated_cost_usd is None


@pytest.mark.unit
def test_sum_transcript_usage_gives_no_estimate_when_one_zero_cost_steps_model_is_unpriced() -> None:
    """A partial estimate is never reported as complete: one step priced, another whose
    own exported model the catalog has no entry for, leaves the whole sample without one."""
    priced_line = json.dumps(
        _export_message(
            session_id="ses_mixed",
            message_id="msg_priced",
            provider_id="openai",
            model_id="gpt-5.6-luna",
            part_id="prt_priced",
            cost=0.0,
            tokens=_ZERO_TOKENS,
        )
    )
    unpriced_line = json.dumps(
        _export_message(
            session_id="ses_mixed",
            message_id="msg_unpriced",
            provider_id="anthropic",
            model_id="claude-unpriced",
            part_id="prt_unpriced",
            cost=0.0,
            tokens=_ZERO_TOKENS,
        )
    )
    adapter = _adapter(model="openai/gpt-5.6-luna", price_catalog=_luna_catalog())

    sample = adapter.sum_transcript_usage([priced_line, unpriced_line], "spawn")

    assert sample.cost_usd is None
    assert sample.estimated_cost_usd is None


@pytest.mark.unit
def test_sum_transcript_usage_keeps_an_export_lines_model_when_a_run_event_repeats_the_step() -> None:
    # One step as an export naming its priced model, then as a run event naming none;
    # the invocation's own model is unpriced, so only the export's pair can price it.
    export_line = json.dumps(
        _export_message(
            session_id="ses_dup",
            message_id="msg_dup",
            provider_id="openai",
            model_id="gpt-5.6-luna",
            part_id="prt_dup",
            cost=0.0,
            tokens=_ZERO_TOKENS,
        )
    )
    event_line = json.dumps(
        _step_finish_event(session_id="ses_dup", part_id="prt_dup", message_id="msg_dup", cost=0.0, tokens=_ZERO_TOKENS)
    )
    adapter = _adapter(model="anthropic/claude-unpriced", price_catalog=_luna_catalog())

    sample = adapter.sum_transcript_usage([export_line, event_line], "spawn")

    assert sample.estimated_cost_usd == pytest.approx((40 * 0.20 + 8 * 1.20) / 1_000_000)


@dataclass
class _CountingPriceCatalog:
    inner: _FakePriceCatalog
    lookups: int = 0

    def price_for(self, provider: str, model: str) -> OpenCodeModelPrice | None:
        self.lookups += 1
        return self.inner.price_for(provider, model)


@pytest.mark.unit
def test_parse_usage_looks_a_model_up_once_however_many_zero_cost_steps_it_prices() -> None:
    events = [
        _step_finish_event(
            session_id="ses_memo", part_id=f"prt_{i}", message_id=f"msg_{i}", cost=0.0, tokens=_ZERO_TOKENS
        )
        for i in range(5)
    ]
    catalog = _CountingPriceCatalog(_luna_catalog())
    adapter = _adapter(model="openai/gpt-5.6-luna", price_catalog=catalog)

    sample = adapter.parse_usage(_jsonl(events), "spawn")

    assert sample is not None
    assert sample.estimated_cost_usd == pytest.approx(5 * (40 * 0.20 + 8 * 1.20) / 1_000_000)
    assert catalog.lookups == 1


# --- classify_usage_limit (blizzard#594) ------------------------------------


@pytest.mark.unit
def test_classify_usage_limit_reads_the_captured_go_upsell_event() -> None:
    now = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)
    output = json.dumps(USAGE_LIMIT_EVENT)

    limit = _adapter().classify_usage_limit(output, [], now)

    assert limit is not None
    assert "usage limit" in limit.detail.lower()
    assert limit.resets_at == now + timedelta(hours=2)


@pytest.mark.unit
def test_classify_usage_limit_is_none_for_an_ordinary_provider_error() -> None:
    now = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)
    payload = _fixture("provider_error")
    output = _jsonl(payload["events"])

    assert _adapter().classify_usage_limit(output, [], now) is None


@pytest.mark.unit
def test_classify_usage_limit_is_none_for_a_429_that_does_not_name_a_usage_limit() -> None:
    now = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)
    output = json.dumps(
        {
            "type": "error",
            "sessionID": "ses_x",
            "error": {"name": "AI_APICallError", "data": {"message": "Too many requests", "statusCode": 429}},
        }
    )

    assert _adapter().classify_usage_limit(output, [], now) is None


@pytest.mark.unit
def test_classify_usage_limit_none_reset_for_an_unparseable_duration_never_raises() -> None:
    now = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)
    output = json.dumps(
        {
            "type": "error",
            "sessionID": "ses_x",
            "error": {"name": "AI_APICallError", "data": {"message": "Usage limit reached.", "statusCode": 429}},
        }
    )

    limit = _adapter().classify_usage_limit(output, [], now)

    assert limit is not None
    assert limit.resets_at is None


@pytest.mark.unit
def test_classify_usage_limit_tolerates_malformed_capture() -> None:
    now = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)
    assert _adapter().classify_usage_limit("not json at all", [], now) is None


# --- classify_provider_overload (blizzard#595) ------------------------------


@pytest.mark.unit
def test_classify_provider_overload_reads_a_529_status_code() -> None:
    output = json.dumps(
        {
            "type": "error",
            "sessionID": "ses_x",
            "error": {"name": "AI_APICallError", "data": {"message": "Overloaded", "statusCode": 529}},
        }
    )

    overload = _adapter().classify_provider_overload(output, [])

    assert overload is not None
    assert overload.detail == "Overloaded"


@pytest.mark.unit
def test_classify_provider_overload_reads_an_overloaded_error_name_without_a_529_status() -> None:
    output = json.dumps(
        {
            "type": "error",
            "sessionID": "ses_x",
            "error": {"name": "OverloadedError", "data": {"message": "the model is overloaded"}},
        }
    )

    assert _adapter().classify_provider_overload(output, []) is not None


@pytest.mark.unit
def test_classify_provider_overload_never_calls_the_transcript_reader() -> None:
    output = json.dumps(
        {
            "type": "error",
            "sessionID": "ses_x",
            "error": {"name": "AI_APICallError", "data": {"message": "Overloaded", "statusCode": 529}},
        }
    )

    class _ExplodingLines(list[str]):
        def __iter__(self) -> Any:
            raise AssertionError("classify_provider_overload must not read the transcript range")

    overload = _adapter().classify_provider_overload(output, _ExplodingLines())

    assert overload is not None


@pytest.mark.unit
def test_classify_provider_overload_is_none_for_an_ordinary_provider_error() -> None:
    payload = _fixture("provider_error")
    output = _jsonl(payload["events"])

    assert _adapter().classify_provider_overload(output, []) is None


@pytest.mark.unit
def test_classify_provider_overload_is_none_for_a_429_usage_limit_refusal() -> None:
    output = json.dumps(USAGE_LIMIT_EVENT)

    assert _adapter().classify_provider_overload(output, []) is None


@pytest.mark.unit
def test_classify_provider_overload_tolerates_malformed_capture() -> None:
    assert _adapter().classify_provider_overload("not json at all", []) is None
