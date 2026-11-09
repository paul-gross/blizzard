"""The OpenCode 2 declaration: the adapter it binds from the shared ``[opencode]`` section, ignoring ``auth_path`` and
``worker_config_path``, and a bundle, publish and scaffold carrying none of the runner's own files."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from blizzard.foundation.clock import IMonotonicClock, ManualMonotonicClock, SystemClock, SystemMonotonicClock
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.binding_selection import BindingSelection
from blizzard.runner.harness.bundle import HarnessBundleError
from blizzard.runner.harness.declaration import SharedHarnessInputs
from blizzard.runner.harness.internal.committed_corpus import CommittedCorpus
from blizzard.runner.harness.internal.process_launcher import ProcessLauncher
from blizzard.runner.harness.opencode.section import OpenCodeSection
from blizzard.runner.harness.opencode_v2.adapter import OpenCodeTwoAdapter
from blizzard.runner.harness.opencode_v2.declaration import OPENCODE_V2_DECLARATION, REQUEST_DIRNAME, driver_command
from blizzard.runner.harness.opencode_v2.health import OpenCodeTwoHealthProbe
from blizzard.runner.harness.opencode_v2.orphan import SessionOrphaner
from blizzard.runner.harness.opencode_v2.price_catalog import SqliteOpenCodeTwoPriceCatalog
from blizzard.runner.harness.opencode_v2.registration import ServiceRegistration
from blizzard.runner.harness.opencode_v2.service import UNLOCATED_DETAIL, ServiceKeeper, ServiceUnavailableError
from blizzard.runner.harness.opencode_v2.transcript.source import OpenCodeTwoTranscriptSource
from blizzard.runner.harness.registry import HarnessAssembly
from blizzard.runner.harness.wiring import HarnessSections, first_bindings, publish_harness_bundle
from blizzard.runner.process.child_env import AllowlistedEnv
from blizzard.runner.process.child_processes import IChildProcesses
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from tests.support.child_launch import CHILDREN

_SECTION = OpenCodeSection(
    enabled=True,
    binary="/opt/opencode",
    model_aliases=(("tier:fast", "openai/gpt-6-mini"),),
    auth_path="/nowhere/auth.json",
    worker_config_path="/nowhere/opencode.json",
)


def _shared(root: Path) -> SharedHarnessInputs:
    return SharedHarnessInputs(
        root=root,
        autonomy=Autonomy.Normal,
        config_dir=None,
        worker_env=AllowlistedEnv.of(()),
        transcripts_root="",
        runner_id="run_7",
    )


def test_the_binding_is_the_opencode_2_adapter_over_the_shared_section(tmp_path: Path) -> None:
    process = LinuxProcessProbe()
    with ThreadPoolExecutor(max_workers=1) as executor:
        binding = OPENCODE_V2_DECLARATION.assemble(
            _SECTION,
            _shared(tmp_path),
            process=process,
            launcher=ProcessLauncher(process, executor=executor, children=CHILDREN),
            children=CHILDREN,
            clock=SystemClock(),
            monotonic_clock=SystemMonotonicClock(),
            health_roots=("",),
            corpus=CommittedCorpus(),
        ).binding
    adapter = binding.adapter
    assert isinstance(adapter, OpenCodeTwoAdapter)
    assert isinstance(binding.transcript_source, OpenCodeTwoTranscriptSource)
    assert adapter.resolve_model(["tier:fast"]) == "openai/gpt-6-mini" and adapter.autonomy is Autonomy.Normal
    command = adapter.resume_command(str(tmp_path), "ses_1")
    assert "attend ses_1" in command and "--opencode /opt/opencode" in command and "/nowhere" not in command
    assert adapter._request_dir == tmp_path / REQUEST_DIRNAME  # pyright: ignore[reportPrivateUsage]
    assert adapter._runner_id == "run_7"  # pyright: ignore[reportPrivateUsage]


def test_the_driver_command_is_the_runners_own_entry_point() -> None:
    (entry,) = driver_command()
    assert Path(entry).name == "blizzard"


def test_scaffold_writes_nothing_and_publish_admits_no_opencode_entry(tmp_path: Path) -> None:
    OPENCODE_V2_DECLARATION.scaffold_runtime(_SECTION, tmp_path, worker_shell="/bin/sh")
    assert list(tmp_path.iterdir()) == []
    bundle = tmp_path / "bundle"
    (bundle / "opencode").mkdir(parents=True)
    root = tmp_path / "root"
    selection = BindingSelection({**first_bindings().chosen, "opencode": OPENCODE_V2_DECLARATION})
    sections = HarnessSections.defaults().replaced(_SECTION)
    publish_harness_bundle(bundle, root, sections=sections, selection=selection)
    (bundle / "opencode" / "opencode.json").write_text("{}")
    with pytest.raises(HarnessBundleError, match="not a recognized entry point"):
        publish_harness_bundle(bundle, root, sections=sections, selection=selection)


def test_health_reads_no_spawn_root_so_one_probe_serves_every_root() -> None:
    """Every check reaches the one managed service, so a probe per root would only repeat it."""
    assert OPENCODE_V2_DECLARATION.health_varies_by_spawn_root is False


def _binding_adapter(root: Path, worker_env: AllowlistedEnv) -> OpenCodeTwoAdapter:
    process = LinuxProcessProbe()
    shared = SharedHarnessInputs(
        root=root,
        autonomy=Autonomy.Normal,
        config_dir=None,
        worker_env=worker_env,
        transcripts_root="",
        runner_id="run_7",
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        binding = OPENCODE_V2_DECLARATION.assemble(
            _SECTION,
            shared,
            process=process,
            launcher=ProcessLauncher(process, executor=executor, children=CHILDREN),
            children=CHILDREN,
            clock=SystemClock(),
            monotonic_clock=SystemMonotonicClock(),
            health_roots=("",),
            corpus=CommittedCorpus(),
        ).binding
    assert isinstance(binding.adapter, OpenCodeTwoAdapter)
    return binding.adapter


def test_the_price_catalog_is_built_from_the_worker_environment_and_only_when_it_names_a_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert _binding_adapter(tmp_path, AllowlistedEnv.of(()))._price_catalog is None  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    adapter = _binding_adapter(tmp_path, AllowlistedEnv.of(("XDG_DATA_HOME",)))
    assert isinstance(adapter._price_catalog, SqliteOpenCodeTwoPriceCatalog)  # pyright: ignore[reportPrivateUsage]


class _NoChildren:
    """Children no assembly or no-location read may run: a service started here would be a defect."""

    def run(self, *args: object, **kwargs: object) -> object:
        raise AssertionError(f"ran a child: {args}")

    def spawn(self, *args: object, **kwargs: object) -> object:
        raise AssertionError(f"spawned a child: {args}")


def _assembly(
    root: Path,
    worker_env: AllowlistedEnv,
    *,
    monotonic_clock: IMonotonicClock | None = None,
    children: IChildProcesses = CHILDREN,
) -> HarnessAssembly:
    process = LinuxProcessProbe()
    shared = SharedHarnessInputs(
        root=root,
        autonomy=Autonomy.Normal,
        config_dir=None,
        worker_env=worker_env,
        transcripts_root="",
        runner_id="run_7",
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        return OPENCODE_V2_DECLARATION.assemble(
            _SECTION,
            shared,
            process=process,
            launcher=ProcessLauncher(process, executor=executor, children=children),
            children=children,
            clock=SystemClock(),
            monotonic_clock=monotonic_clock or ManualMonotonicClock(),
            health_roots=("",),
            corpus=CommittedCorpus(),
        )


def _parts(
    assembly: HarnessAssembly,
) -> tuple[OpenCodeTwoAdapter, OpenCodeTwoHealthProbe, ServiceKeeper, OpenCodeTwoTranscriptSource, SessionOrphaner]:
    adapter = assembly.binding.adapter
    (probe,) = assembly.health_probes
    assert isinstance(adapter, OpenCodeTwoAdapter) and isinstance(probe, OpenCodeTwoHealthProbe)
    keeper = adapter._keeper  # pyright: ignore[reportPrivateUsage]
    source = adapter.transcript_source()
    orphaner = adapter._orphaner  # pyright: ignore[reportPrivateUsage]
    assert isinstance(keeper, ServiceKeeper)
    assert isinstance(source, OpenCodeTwoTranscriptSource) and isinstance(orphaner, SessionOrphaner)
    return adapter, probe, keeper, source, orphaner


def _register(state: Path, registration_id: str, password: str) -> None:
    path = state / "opencode" / "service.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'{{"id": "{registration_id}", "version": "2.1.4", "url": "http://127.0.0.1:4096", '
        f'"pid": 100, "password": "{password}"}}'
    )


def _identity(registration: ServiceRegistration | None) -> tuple[str, str] | None:
    return None if registration is None else (registration.id, registration.password)


def test_one_assembly_shares_its_hold_keeper_source_transport_and_clock(tmp_path: Path) -> None:
    clock = ManualMonotonicClock()
    assembly = _assembly(tmp_path, AllowlistedEnv.of(()), monotonic_clock=clock, children=_NoChildren())  # pyright: ignore[reportArgumentType]
    adapter, probe, keeper, source, orphaner = _parts(assembly)
    assert probe._keeper is keeper  # pyright: ignore[reportPrivateUsage]
    assert probe._hold is adapter._hold  # pyright: ignore[reportPrivateUsage]
    assert assembly.binding.transcript_source is source
    transport = keeper._transport  # pyright: ignore[reportPrivateUsage]
    assert source._transport is transport and orphaner._transport is transport  # pyright: ignore[reportPrivateUsage]
    assert keeper._clock is clock and source._clock is clock and orphaner._clock is clock  # pyright: ignore[reportPrivateUsage]
    # The two live-profile readers share one registration reader; the keeper reads its own snapshot.
    assert source._source is orphaner._source  # pyright: ignore[reportPrivateUsage]
    assert keeper._source is not source._source  # pyright: ignore[reportPrivateUsage]


def test_two_assemblies_share_no_hold_keeper_or_transport(tmp_path: Path) -> None:
    first, second = (_parts(_assembly(tmp_path, AllowlistedEnv.of(()))) for _ in range(2))
    assert first[0]._hold is not second[0]._hold  # pyright: ignore[reportPrivateUsage]
    assert first[2] is not second[2]
    assert first[2]._transport is not second[2]._transport  # pyright: ignore[reportPrivateUsage]


def test_the_keeper_reads_its_snapshot_while_the_source_and_orphaner_read_the_live_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    booted, moved = tmp_path / "booted", tmp_path / "moved"
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(booted))
    _register(booted, "svc_booted", "pw_booted")
    _register(moved, "svc_moved", "pw_moved")
    _, _, keeper, source, orphaner = _parts(_assembly(tmp_path, AllowlistedEnv.of(("XDG_STATE_HOME",))))
    monkeypatch.setenv("XDG_STATE_HOME", str(moved))
    assert _identity(keeper._source()) == ("svc_booted", "pw_booted")  # pyright: ignore[reportPrivateUsage]
    assert _identity(source._source()) == ("svc_moved", "pw_moved")  # pyright: ignore[reportPrivateUsage]
    assert _identity(orphaner._source()) == ("svc_moved", "pw_moved")  # pyright: ignore[reportPrivateUsage]


def test_every_reader_rereads_the_registration_after_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    _, _, keeper, source, orphaner = _parts(_assembly(tmp_path, AllowlistedEnv.of(("XDG_STATE_HOME",))))
    readers = (keeper._source, source._source, orphaner._source)  # pyright: ignore[reportPrivateUsage]
    assert [read() for read in readers] == [None, None, None]
    _register(state, "svc_1", "pw_1")
    assert [_identity(read()) for read in readers] == [("svc_1", "pw_1")] * 3
    _register(state, "svc_2", "pw_2")
    assert [_identity(read()) for read in readers] == [("svc_2", "pw_2")] * 3


def test_home_locates_the_registration_when_xdg_state_home_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    _register(tmp_path / ".local" / "state", "svc_home", "pw_home")
    _, _, keeper, source, _ = _parts(_assembly(tmp_path, AllowlistedEnv.of(())))
    assert _identity(keeper._source()) == ("svc_home", "pw_home")  # pyright: ignore[reportPrivateUsage]
    assert _identity(source._source()) == ("svc_home", "pw_home")  # pyright: ignore[reportPrivateUsage]


def test_no_location_builds_the_runner_but_reports_no_service_and_never_starts_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    assembly = _assembly(tmp_path, AllowlistedEnv.of(()), children=_NoChildren())  # pyright: ignore[reportArgumentType]
    _, _, keeper, source, orphaner = _parts(assembly)
    for call in (keeper.answering, keeper.ensure, keeper.listed_models):
        with pytest.raises(ServiceUnavailableError) as raised:
            call()
        assert str(raised.value) == UNLOCATED_DETAIL
    assert source._source() is None and orphaner._source() is None  # pyright: ignore[reportPrivateUsage]
    assert orphaner.orphan("ses_1") is False
