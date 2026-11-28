"""A host graph with its runners already composed over it, for the tests that drive a host's runners — their loops,
stores and settings — without opening the host root a served host composes them under; the same host opened under
the production host root with a child for each runner, for the tests that serve it; and the production host root
over a host, for the tests that start one more runner on it."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.runner import composition
from blizzard.runner.composition import (
    HostProcess,
    IRunnerStart,
    RunnerCompose,
    RunnerProcess,
    StartupNote,
    start_runner,
)
from blizzard.runner.harness.binding_selection import BindingSelection
from blizzard.runner.harness.bundle import BundleSnapshot
from blizzard.runner.host_config import BuiltRunner, HostConfig
from blizzard.runner.host_root import HostRoot, OpenedHost
from blizzard.runner.host_root import open_host as open_production_host
from tests.support.host_runner_graph import FakeResume, FakeWiring


def build_host_process(
    host: HostConfig,
    runners: Sequence[BuiltRunner] = (),
    *,
    bundles: Mapping[str, BundleSnapshot | None] | None = None,
    selections: Mapping[str, BindingSelection] | None = None,
    publish_bundles: bool = False,
    environ: Mapping[str, str] | None = None,
    trace_exporter: ITraceExporter | None = None,
    **host_options: Any,
) -> HostProcess:
    """The host ``host`` declares, and each of ``runners`` composed over it under the options a boot would compose it
    with; closing the host closes them. ``host_options`` are :func:`~blizzard.runner.composition.build_host_process`'s."""
    process = composition.build_host_process(host, environ=environ, **host_options)
    compose = _compose(runners, bundles, selections, publish_bundles, environ, trace_exporter)
    try:
        for built in runners:
            process.runners = (*process.runners, compose.runner(host, process, built))
    except BaseException:
        process.close()
        raise
    return process


def open_host(
    host: HostConfig,
    runners: Sequence[BuiltRunner] = (),
    *,
    bundles: Mapping[str, BundleSnapshot | None] | None = None,
    selections: Mapping[str, BindingSelection] | None = None,
    publish_bundles: bool = False,
    environ: Mapping[str, str] | None = None,
    trace_exporter: ITraceExporter | None = None,
    **host_options: Any,
) -> OpenedHost:
    """The host ``host`` declares, opened under the production host root with a child for each of ``runners``, each
    composed under the options a boot would compose it with; a served app over it serves each, and closing the opened
    host closes them, the root and the host. The arguments are :func:`build_host_process`'s."""
    process = composition.build_host_process(host, environ=environ, **host_options)
    compose = _compose(runners, bundles, selections, publish_bundles, environ, trace_exporter)
    return open_production_host(process, host, runners, compose=compose)


def _compose(
    runners: Sequence[BuiltRunner],
    bundles: Mapping[str, BundleSnapshot | None] | None,
    selections: Mapping[str, BindingSelection] | None,
    publish_bundles: bool,
    environ: Mapping[str, str] | None,
    trace_exporter: ITraceExporter | None,
) -> RunnerCompose:
    return RunnerCompose(
        publish=frozenset(built.id for built in runners) if publish_bundles else frozenset(),
        bundles=dict(bundles or {}),
        selections=dict(selections or {}),
        environ=environ,
        trace_exporter=trace_exporter,
    )


class _QuietStart:
    """The start seams a runner started on a served-app-free host reaches: no resume work, no scratch, nothing said."""

    def resume(self, runner: RunnerProcess) -> FakeResume:
        return FakeResume()

    def reconcile_scratch(self, runner: RunnerProcess) -> None:
        return None

    def announce(self, note: StartupNote) -> None:
        return None


def serving_root(
    host_config: HostConfig, host: HostProcess, compose: RunnerCompose | None = None
) -> HostRoot[FakeWiring, FakeResume]:
    """The production host root over ``host``; closing it closes each runner started on it, then its process scope."""
    return HostRoot[FakeWiring, FakeResume].serving(host, host_config, compose=compose)


def start_on(root: HostRoot[FakeWiring, FakeResume], built: BuiltRunner) -> RunnerProcess:
    """``built`` started on ``root`` as a reconcile starts one — its child opened and its runner composed and placed —
    and never served. Raises :class:`~blizzard.runner.composition.RunnerStartFailed`."""
    start = cast("IRunnerStart[FakeWiring, FakeResume]", _QuietStart())
    return start_runner(built, start, root.runners).live.runner
