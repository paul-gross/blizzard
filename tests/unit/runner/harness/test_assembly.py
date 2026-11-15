"""One runner's harness assembly: each enabled, selected binding assembled once, one health probe per root it reads,
and every metadata read leaving assembly alone (unit)."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from concurrent.futures import Executor
from pathlib import Path
from typing import IO, Any

import pytest

from blizzard.foundation.clock import IClock, IMonotonicClock, ManualMonotonicClock, SystemClock
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.adapter import EverySpawnRootProbe, IHarnessHealthProbe
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.binding_selection import BindingSelection, IVersionProbe
from blizzard.runner.harness.bundle import HarnessLayout, HarnessSource
from blizzard.runner.harness.claude_code.declaration import CLAUDE_CODE_DECLARATION
from blizzard.runner.harness.claude_code.health import ClaudeCodeHealthProbe
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.declaration import HarnessSection, IHarnessDeclaration, SharedHarnessInputs
from blizzard.runner.harness.harness_telemetry_plan import HarnessTelemetryNames, HarnessTelemetryPlan
from blizzard.runner.harness.internal.committed_corpus import CommittedCorpus
from blizzard.runner.harness.internal.process_launcher import ProcessLauncher
from blizzard.runner.harness.offline_compatibility import ICompatibilityCorpus
from blizzard.runner.harness.opencode.declaration import OPENCODE_DECLARATION
from blizzard.runner.harness.opencode.health import OpenCodeHealthProbe
from blizzard.runner.harness.opencode.section import OPENCODE_HARNESS_ID
from blizzard.runner.harness.opencode_v2.declaration import OPENCODE_V2_DECLARATION
from blizzard.runner.harness.opencode_v2.health import OpenCodeTwoHealthProbe
from blizzard.runner.harness.process_launch import IProcessLauncher
from blizzard.runner.harness.registry import HarnessAssembly, HarnessBinding, HarnessRegistry
from blizzard.runner.harness.wiring import (
    HarnessSections,
    HarnessSettings,
    build_production_harnesses,
    bundle_layouts,
    combined_telemetry_plan,
    declared,
    declared_normalizer_versions,
    declared_telemetry_names,
    enabled,
    first_bindings,
    harness_catalog,
    inspect_harness_bundle,
    publish_harness_bundle,
    shared_inputs,
)
from blizzard.runner.process.child_env import ChildEnv
from blizzard.runner.process.child_processes import (
    ChildRun,
    IChildProcesses,
    IPtyChild,
    ISpawnedChild,
    ShellCommand,
    Stream,
    StreamTarget,
)
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from blizzard.runner.process.probe import IProcessProbe
from tests.support.harness_sections import claude_code, with_claude_code, with_opencode
from tests.support.runner_fakes import NO_TRACING


class _RefusingChildren:
    """A launch seam no assembly or metadata read may reach: every launch fails the test."""

    def run(
        self,
        command: Sequence[str] | ShellCommand,
        *,
        cwd: Path | str | None,
        env: ChildEnv,
        timeout: float,
        input: str | None = None,
        input_settle: float = 0.0,
        stdout: IO[bytes] | None = None,
    ) -> ChildRun:
        raise AssertionError(f"ran {command!r}")

    def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | str | None,
        env: ChildEnv,
        stdin: StreamTarget = Stream.DEVNULL,
        stdout: StreamTarget = Stream.DEVNULL,
        stderr: StreamTarget = Stream.DEVNULL,
        child_setup: Callable[[], None] | None = None,
        pass_fds: tuple[int, ...] = (),
    ) -> ISpawnedChild:
        raise AssertionError(f"spawned {argv!r}")

    def spawn_pty(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | str,
        env: ChildEnv,
        child_setup: Callable[[], None] | None = None,
    ) -> IPtyChild:
        raise AssertionError(f"spawned {argv!r}")

    def run_foreground(self, command: ShellCommand, *, cwd: Path | str, env: ChildEnv) -> int:
        raise AssertionError(f"ran {command!r}")


_children_conforms: type[IChildProcesses] = _RefusingChildren


class _StubProbe:
    """A health probe standing for one root; never evaluated here."""

    def __init__(self, root: str) -> None:
        self.root = root

    def binary_present(self) -> bool:
        return True

    def probe_authentication(self) -> bool:
        return True

    def config_conflicts(self) -> tuple[str, ...]:
        return (f"conflict at {self.root!r}",)

    def supported_version(self) -> None:
        return None

    def supported_version_display(self) -> str | None:
        return None

    def normalize_version(self, raw: str | None) -> str | None:
        return raw

    def classifies_offline(self) -> bool:
        return False

    def declared_degradations(self) -> tuple[()]:
        return ()


_probe_conforms: type[IHarnessHealthProbe] = _StubProbe


@dataclasses.dataclass(frozen=True)
class _Assembled:
    """One recorded ``assemble`` call."""

    harness_id: str
    health_roots: tuple[str, ...]
    corpus: ICompatibilityCorpus
    monotonic_clock: IMonotonicClock


class _RecordingDeclaration:
    """A declaration answering every metadata read as ``inner`` does, and recording each ``assemble`` call — answered
    with a stub probe per root, or ``extra_probes`` more or fewer."""

    def __init__(
        self,
        inner: IHarnessDeclaration[Any],
        calls: list[_Assembled],
        *,
        varies_by_root: bool,
        extra_probes: int = 0,
    ) -> None:
        self._inner = inner
        self._calls = calls
        self._varies_by_root = varies_by_root
        self._extra_probes = extra_probes

    @property
    def harness_id(self) -> str:
        return self._inner.harness_id

    @property
    def service_version(self) -> bool:
        return self._inner.service_version

    @property
    def normalizer_version(self) -> str:
        return self._inner.normalizer_version

    @property
    def bundle_layout(self) -> HarnessLayout:
        return self._inner.bundle_layout

    @property
    def telemetry_names(self) -> HarnessTelemetryNames | None:
        return self._inner.telemetry_names

    def telemetry_plan(self, section: HarnessSection, shared: SharedHarnessInputs) -> HarnessTelemetryPlan:
        return self._inner.telemetry_plan(section, shared)

    def assemble(
        self,
        section: HarnessSection,
        shared: SharedHarnessInputs,
        *,
        process: IProcessProbe,
        launcher: IProcessLauncher,
        children: IChildProcesses,
        clock: IClock,
        monotonic_clock: IMonotonicClock,
        health_roots: tuple[str, ...],
        corpus: ICompatibilityCorpus,
        versions: IVersionProbe | None = None,
    ) -> HarnessAssembly:
        self._calls.append(_Assembled(self.harness_id, health_roots, corpus, monotonic_clock))
        roots = (*health_roots, *health_roots[:1] * self._extra_probes)
        if self._extra_probes < 0:
            roots = health_roots[: len(health_roots) + self._extra_probes]
        return HarnessAssembly(binding=HarnessBinding(), health_probes=tuple(_StubProbe(root) for root in roots))

    @property
    def health_varies_by_spawn_root(self) -> bool:
        return self._varies_by_root

    def publish_layout(self, section: HarnessSection, *, autonomy: Autonomy, runtime_root: Path) -> HarnessLayout:
        return self._inner.publish_layout(section, autonomy=autonomy, runtime_root=runtime_root)

    def scaffold_runtime(self, section: HarnessSection, root: Path, *, worker_shell: str) -> None:
        self._inner.scaffold_runtime(section, root, worker_shell=worker_shell)

    def diagnostics(self, section: HarnessSection, shared: SharedHarnessInputs, *, spawn_root: str) -> tuple[str, ...]:
        return self._inner.diagnostics(section, shared, spawn_root=spawn_root)

    def unbundled_status(self, section: HarnessSection) -> tuple[str, ...]:
        return self._inner.unbundled_status(section)

    def snapshot_status(self, snapshot: Path, sources: tuple[HarnessSource, ...]) -> tuple[str, ...]:
        return self._inner.snapshot_status(snapshot, sources)


_declaration_conforms: type[IHarnessDeclaration[Any]] = _RecordingDeclaration


def _recording(
    calls: list[_Assembled], *, claude_varies: bool = True, opencode_varies: bool = False, extra_probes: int = 0
) -> BindingSelection:
    return BindingSelection(
        {
            CLAUDE_CODE_HARNESS_ID: _RecordingDeclaration(
                CLAUDE_CODE_DECLARATION, calls, varies_by_root=claude_varies, extra_probes=extra_probes
            ),
            OPENCODE_HARNESS_ID: _RecordingDeclaration(
                OPENCODE_V2_DECLARATION, calls, varies_by_root=opencode_varies, extra_probes=extra_probes
            ),
        }
    )


def _settings(tmp_path: Path, config: RunnerConfig | None = None) -> HarnessSettings:
    return (config or RunnerConfig(root=tmp_path, db_url="sqlite://")).harness_settings(NO_TRACING)


def _build(
    settings: HarnessSettings,
    selection: BindingSelection,
    executor: Executor,
    *,
    spawn_roots: Sequence[str] = ("",),
    corpus: ICompatibilityCorpus | None = None,
    monotonic_clock: IMonotonicClock | None = None,
) -> tuple[HarnessRegistry, dict[str, IHarnessHealthProbe]]:
    return build_production_harnesses(
        settings,
        selection=selection,
        executor=executor,
        process=LinuxProcessProbe(),
        children=_RefusingChildren(),
        clock=SystemClock(),
        monotonic_clock=monotonic_clock or ManualMonotonicClock(),
        spawn_roots=spawn_roots,
        corpus=corpus or CommittedCorpus(),
    )


def test_each_enabled_selected_binding_is_assembled_once_with_the_one_corpus_and_clock(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    calls: list[_Assembled] = []
    corpus, clock = CommittedCorpus(), ManualMonotonicClock()

    registry, probes = _build(
        _settings(tmp_path),
        _recording(calls),
        spawn_executor,
        spawn_roots=("a", "b"),
        corpus=corpus,
        monotonic_clock=clock,
    )

    assert [call.harness_id for call in calls] == [CLAUDE_CODE_HARNESS_ID, OPENCODE_HARNESS_ID]
    assert all(call.corpus is corpus and call.monotonic_clock is clock for call in calls)
    assert registry.known_harnesses == (CLAUDE_CODE_HARNESS_ID, OPENCODE_HARNESS_ID)
    assert list(probes) == [CLAUDE_CODE_HARNESS_ID, OPENCODE_HARNESS_ID]


def _without_claude_code(config: RunnerConfig) -> RunnerConfig:
    return with_claude_code(config, enabled=False)


def _without_opencode(config: RunnerConfig) -> RunnerConfig:
    return with_opencode(config, enabled=False)


@pytest.mark.parametrize(
    ("config", "assembled"),
    [(_without_claude_code, [OPENCODE_HARNESS_ID]), (_without_opencode, [CLAUDE_CODE_HARNESS_ID])],
)
def test_a_disabled_binding_is_never_assembled(
    tmp_path: Path,
    spawn_executor: Executor,
    config: Callable[[RunnerConfig], RunnerConfig],
    assembled: list[str],
) -> None:
    calls: list[_Assembled] = []

    registry, probes = _build(
        _settings(tmp_path, config(RunnerConfig(root=tmp_path, db_url="sqlite://"))), _recording(calls), spawn_executor
    )

    assert [call.harness_id for call in calls] == assembled
    assert registry.known_harnesses == tuple(assembled)
    assert list(probes) == assembled


def test_the_selection_given_is_the_one_assembled_and_it_is_not_reselected(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    """The binary's version is never asked: the refusing launch seam would fail any probe or reselection."""
    calls: list[_Assembled] = []
    selection = _recording(calls)
    chosen = dict(selection.chosen)

    _build(_settings(tmp_path), selection, spawn_executor)

    assert dict(selection.chosen) == chosen
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("spawn_roots", "varying", "single"),
    [
        ((), ("",), ("",)),
        (("a", "a", "b", "a"), ("a", "b"), ("a",)),
        (("b", "a", "c"), ("b", "a", "c"), ("b",)),
        (("a", "", "a"), ("a", ""), ("a",)),
    ],
)
def test_roots_are_deduplicated_in_first_order_and_only_a_varying_binding_receives_them_all(
    tmp_path: Path,
    spawn_executor: Executor,
    spawn_roots: tuple[str, ...],
    varying: tuple[str, ...],
    single: tuple[str, ...],
) -> None:
    calls: list[_Assembled] = []

    _, probes = _build(_settings(tmp_path), _recording(calls), spawn_executor, spawn_roots=spawn_roots)

    assert [call.health_roots for call in calls] == [varying, single]
    claude = probes[CLAUDE_CODE_HARNESS_ID]
    if len(varying) == 1:
        assert isinstance(claude, _StubProbe) and claude.root == varying[0]
    else:
        assert isinstance(claude, EverySpawnRootProbe)
        assert claude.config_conflicts() == tuple(f"conflict at {root!r}" for root in varying)
    opencode = probes[OPENCODE_HARNESS_ID]
    assert isinstance(opencode, _StubProbe) and opencode.root == single[0]


def test_root_variation_alone_decides_how_many_roots_a_binding_receives(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    calls: list[_Assembled] = []

    _build(
        _settings(tmp_path),
        _recording(calls, claude_varies=False, opencode_varies=True),
        spawn_executor,
        spawn_roots=("a", "b"),
    )

    assert [call.health_roots for call in calls] == [("a",), ("a", "b")]


@pytest.mark.parametrize("extra_probes", [1, -1, -2])
def test_an_assembly_owing_a_different_number_of_probes_than_roots_is_refused(
    tmp_path: Path, spawn_executor: Executor, extra_probes: int
) -> None:
    with pytest.raises(RuntimeError, match="owes one per root"):
        _build(
            _settings(tmp_path),
            _recording([], extra_probes=extra_probes),
            spawn_executor,
            spawn_roots=("a", "b"),
        )


def test_every_spawn_root_probe_unions_conflicts_in_order_and_delegates_the_rest_to_the_first() -> None:
    first, second = _StubProbe("a"), _StubProbe("b")
    probe = EverySpawnRootProbe((first, second, _StubProbe("a")))

    assert probe.config_conflicts() == ("conflict at 'a'", "conflict at 'b'")
    assert probe.normalize_version("1.2") == "1.2"
    assert probe.classifies_offline() is False


def test_real_claude_code_assembles_one_ambient_probe_per_distinct_root(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    roots = (str(tmp_path / "a"), str(tmp_path / "b"))

    assembly = CLAUDE_CODE_DECLARATION.assemble(
        claude_code(RunnerConfig(root=tmp_path, db_url="sqlite://")),
        shared_inputs(_settings(tmp_path)),
        process=LinuxProcessProbe(),
        launcher=_launcher(spawn_executor),
        children=_RefusingChildren(),
        clock=SystemClock(),
        monotonic_clock=ManualMonotonicClock(),
        health_roots=roots,
        corpus=CommittedCorpus(),
    )

    assert all(isinstance(probe, ClaudeCodeHealthProbe) for probe in assembly.health_probes)
    project_dirs = [
        probe._ambient_sources.project_dir  # pyright: ignore[reportPrivateUsage]
        for probe in assembly.health_probes
        if isinstance(probe, ClaudeCodeHealthProbe) and probe._ambient_sources is not None  # pyright: ignore[reportPrivateUsage]
    ]
    assert project_dirs == [Path(root) for root in roots]

    _, probes = _build(
        _settings(tmp_path),
        first_bindings(),
        spawn_executor,
        spawn_roots=(*roots, roots[0]),
    )
    assert isinstance(probes[CLAUDE_CODE_HARNESS_ID], EverySpawnRootProbe)
    _, single = _build(_settings(tmp_path), first_bindings(), spawn_executor, spawn_roots=())
    assert isinstance(single[CLAUDE_CODE_HARNESS_ID], ClaudeCodeHealthProbe)


@pytest.mark.parametrize(
    ("declaration", "probe_type"),
    [(OPENCODE_DECLARATION, OpenCodeHealthProbe), (OPENCODE_V2_DECLARATION, OpenCodeTwoHealthProbe)],
)
def test_real_opencode_bindings_assemble_one_probe_for_every_root(
    tmp_path: Path, spawn_executor: Executor, declaration: IHarnessDeclaration[Any], probe_type: type[object]
) -> None:
    selection = BindingSelection({**first_bindings().chosen, OPENCODE_HARNESS_ID: declaration})

    _, probes = _build(_settings(tmp_path), selection, spawn_executor, spawn_roots=("a", "b", "c"))

    assert declaration.health_varies_by_spawn_root is False
    assert isinstance(probes[OPENCODE_HARNESS_ID], probe_type)


def _launcher(executor: Executor) -> IProcessLauncher:
    return ProcessLauncher(LinuxProcessProbe(), executor=executor, children=_RefusingChildren())


def test_catalog_declarations_hold_no_runtime_state() -> None:
    for declaration in harness_catalog():
        assert dataclasses.is_dataclass(declaration)
        assert dataclasses.fields(declaration) == ()


def test_metadata_publish_init_and_catalog_reads_assemble_nothing(tmp_path: Path) -> None:
    calls: list[_Assembled] = []
    selection = _recording(calls)
    settings = _settings(tmp_path)
    sections = HarnessSections.defaults()
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    paired = list(declared(sections, selection))
    list(enabled(sections, selection))
    combined_telemetry_plan(settings, selection=selection)
    bundle_layouts(selection)
    declared_normalizer_versions()
    declared_telemetry_names()
    publish_harness_bundle(bundle, runtime, selection=selection, sections=sections)
    sources = inspect_harness_bundle(bundle, selection)
    shared = shared_inputs(settings)
    for declaration, section in paired:
        declaration.scaffold_runtime(section, runtime, worker_shell="/bin/sh")
        declaration.diagnostics(section, shared, spawn_root=str(tmp_path))
        declaration.unbundled_status(section)
        declaration.snapshot_status(runtime, sources)

    assert calls == []
