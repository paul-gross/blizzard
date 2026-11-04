"""Runner-owned OpenCode commands: compatibility diagnostics, the OpenCode 2 turn driver, and OpenCode 2 takeover."""

from __future__ import annotations

import os
import shutil
import signal
import sys
import tempfile
import threading
from functools import partial
from pathlib import Path

import click

from blizzard.runner.cli.children import RunnerCliCollaborators
from blizzard.runner.cli.worker import send_heartbeat
from blizzard.runner.harness.admission import version_admitted
from blizzard.runner.harness.compatibility import CompatibilityContractError
from blizzard.runner.harness.opencode.compatibility.attach import LoopbackAttachProxyFactory
from blizzard.runner.harness.opencode.compatibility.compaction import SubprocessOpenCodeCompactor
from blizzard.runner.harness.opencode.compatibility.diagnostic import run_opencode_compatibility
from blizzard.runner.harness.opencode.compatibility.evidence import OpenCodeEvidence, OpenCodeEvidenceError
from blizzard.runner.harness.opencode.compatibility.probe import (
    ADMITTED_OPENCODE_RANGE,
    ADMITTED_OPENCODE_RANGE_DISPLAY,
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    OpenCodeCompatibilityProbe,
    OpenCodeProbeError,
)
from blizzard.runner.harness.opencode.compatibility.process import SubprocessOpenCodeProcess, stop_started_process
from blizzard.runner.harness.opencode.compatibility.scratch_git import SubprocessOpenCodeScratchGit
from blizzard.runner.harness.opencode.paths import resolve_opencode_auth_path
from blizzard.runner.harness.opencode.version import normalize_opencode_version
from blizzard.runner.harness.opencode_v2.attend import (
    DEFAULT_OPENCODE_BINARY,
    AttendError,
    SessionHandover,
    attended_argv,
    parse_model_option,
)
from blizzard.runner.harness.opencode_v2.client import ModelReference
from blizzard.runner.harness.opencode_v2.compatibility.corpus import (
    CorpusError,
    ProofEvidence,
    Sanitizer,
    validate_evidence_directory,
)
from blizzard.runner.harness.opencode_v2.compatibility.proof import OpenCodeTwoProof
from blizzard.runner.harness.opencode_v2.compatibility.rows import classify_row
from blizzard.runner.harness.opencode_v2.compatibility.store_migration import StoreMigrationProbe
from blizzard.runner.harness.opencode_v2.compatibility.subprocess_host import SubprocessProofHost
from blizzard.runner.harness.opencode_v2.drive import TurnDriver
from blizzard.runner.harness.opencode_v2.driver import (
    BIND_FAULTS,
    DriverCore,
    ServiceIdentityError,
    ServiceNotRegisteredError,
)
from blizzard.runner.harness.opencode_v2.errors import ErrorKind, TurnError
from blizzard.runner.harness.opencode_v2.registration import (
    RegistrationError,
    ServiceRegistration,
    read_registration,
    registration_path,
)
from blizzard.runner.harness.opencode_v2.request import DriveRequestError, take_request
from blizzard.runner.harness.opencode_v2.result import Outcome, ResultWriter
from blizzard.runner.harness.opencode_v2.transport import (
    UrllibServiceTransport,
)
from blizzard.runner.harness.opencode_v2.version import (
    ADMITTED_OPENCODE_V2_RANGE,
    ADMITTED_OPENCODE_V2_RANGE_TEXT,
    normalize_opencode_v2_version,
)
from blizzard.runner.process.child_env import AllowlistedEnv, ChildEnv
from blizzard.runner.process.child_processes import IChildProcesses


@click.group("opencode")
def opencode_group() -> None:
    """OpenCode's admitted-version compatibility diagnostics, for either major version, the OpenCode 2 turn driver, and
    OpenCode 2 takeover."""


@opencode_group.command("compatibility")
@click.option(
    "--binary",
    "binary",
    required=True,
    type=click.Path(exists=True, dir_okay=False, resolve_path=True, path_type=Path),
    help="Explicit OpenCode executable to probe.",
)
@click.option("--model", "model", required=True, help="Explicit provider/model reference.")
@click.option("--variant", "variant", required=True, help="Explicit OpenCode model variant.")
@click.option(
    "--evidence-dir",
    "evidence_directory",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory for sanitized proof evidence.",
)
@click.option(
    "--live-provider",
    "--allow-live-provider",
    "live_provider",
    is_flag=True,
    required=True,
    help="Required explicit opt-in to provider-reaching probes.",
)
@click.option(
    "--opencode1-binary",
    "opencode1_binary",
    type=click.Path(exists=True, dir_okay=False, resolve_path=True, path_type=Path),
    help="OpenCode 2 only: an OpenCode 1 executable that seeds the disposable store the migration row opens.",
)
@click.option(
    "--opencode1-model",
    "opencode1_model",
    help="OpenCode 2 only: the provider/model OpenCode 1 seeds that store with.",
)
@click.pass_context
def opencode_compatibility(
    ctx: click.Context,
    binary: Path,
    model: str,
    variant: str,
    evidence_directory: Path,
    live_provider: bool,
    opencode1_binary: Path | None,
    opencode1_model: str | None,
) -> None:
    """Prove an admitted OpenCode version in a disposable git repository and retain sanitized evidence.

    The binary's major version selects the proof: OpenCode 1 runs in an isolated scratch home, OpenCode 2 on the
    operator's own managed service."""
    provider, _, model_id = model.partition("/")
    if not provider or not model_id:
        raise click.BadParameter("expected provider/model", param_hint="--model")
    carrier = RunnerCliCollaborators.of(ctx)
    children = carrier.children
    tool_env = AllowlistedEnv.of(_OPENCODE_DIRECTORY_VARIABLES).tool()
    raw = _isolated_version(children, binary)
    v1_version, v2_version = normalize_opencode_version(raw), normalize_opencode_v2_version(raw)
    if v2_version is not None and version_admitted(v2_version, ADMITTED_OPENCODE_V2_RANGE):
        _opencode_two_compatibility(
            children,
            tool_env,
            binary,
            ModelReference(provider, model_id, variant),
            evidence_directory,
            opencode1_binary,
            opencode1_model,
        )
        return
    if v1_version is None or not version_admitted(v1_version, ADMITTED_OPENCODE_RANGE):
        observed = v1_version or v2_version or raw.strip() or "no version"
        click.echo(f"OpenCode version: {observed}")
        click.echo(
            f"compatibility: blocking — outside every admitted range "
            f"(OpenCode 1 {ADMITTED_OPENCODE_RANGE_DISPLAY}, OpenCode 2 {ADMITTED_OPENCODE_V2_RANGE_TEXT}); "
            "nothing was probed"
        )
        raise click.exceptions.Exit(1)
    try:
        process = SubprocessOpenCodeProcess(children)
        transport = carrier.loopback
        probe = OpenCodeCompatibilityProbe(
            binary=str(binary),
            model=model,
            variant=variant,
            # No operator passthrough: the scratch repository needs nothing beyond the base allowlist.
            scratch_git=SubprocessOpenCodeScratchGit(children, AllowlistedEnv.of(()).tool()),
            process=process,
            compactor=SubprocessOpenCodeCompactor(
                process,
                stop_started_process,
                transport,
                timeout_seconds=DEFAULT_COMMAND_TIMEOUT_SECONDS,
            ),
            transport=transport,
            attach_proxy_factory=LoopbackAttachProxyFactory(transport),
            # This short-lived command is its own composition root and runs as the operator.
            auth_source=resolve_opencode_auth_path(os.environ),
            allow_live_provider=live_provider,
        )
        report = run_opencode_compatibility(
            probe,
            OpenCodeEvidence(evidence_directory, secrets=probe.secret_values),
        )
    except (CompatibilityContractError, OpenCodeEvidenceError, OpenCodeProbeError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"OpenCode version: {report.observed_version}")
    for result in report.results:
        click.echo(f"{result.probe.value}: {result.classification.value} ({result.state.value}) — {result.summary}")
    click.echo(f"compatibility: {report.classification.value}")
    if not report.admissible:
        raise click.exceptions.Exit(1)


#: The directories OpenCode resolves its config, store, and service registration from; the operator's, passed through.
_OPENCODE_DIRECTORY_VARIABLES = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME")


def _opencode_two_compatibility(
    children: IChildProcesses,
    tool_env: ChildEnv,
    binary: Path,
    model: ModelReference,
    evidence_directory: Path,
    opencode1_binary: Path | None,
    opencode1_model: str | None,
) -> None:
    passwords: set[str] = set()
    source = partial(_registration, registration_path(os.environ), passwords)
    host = SubprocessProofHost(children, tool_env)
    scratch_root = Path(tempfile.mkdtemp(prefix="blizzard-opencode2-proof-"))
    try:
        scratch = scratch_root / "repo"
        scratch.mkdir()
        (scratch / "README.md").write_text("blizzard compatibility proof\n")
        host.run(["git", "init", "-q", "."], cwd=scratch, timeout=30)
        transport = UrllibServiceTransport()
        proof = OpenCodeTwoProof(
            binary=str(binary),
            model=model,
            scratch=scratch,
            host=host,
            core_factory=lambda: DriverCore(source, transport),
            migration=StoreMigrationProbe(
                binary=str(binary),
                opencode1=str(opencode1_binary) if opencode1_binary else None,
                opencode1_model=opencode1_model,
                root=scratch_root / "store-migration",
                host=host,
            ),
        )
        validate_evidence_directory(evidence_directory)
        report = proof.run()
        source()
        home = os.environ.get("HOME", "")
        evidence = ProofEvidence(
            evidence_directory,
            Sanitizer(
                secrets=tuple(sorted(passwords)),
                replacements=(
                    (str(scratch_root), "<scratch>"),
                    (str(evidence_directory), "<evidence>"),
                    *(((home, "<home>"),) if home else ()),
                ),
            ),
        )
        evidence.write(report, proof.captures, model=f"{model.provider_id}/{model.id}#{model.variant}")
    except (CorpusError, RegistrationError, ServiceNotRegisteredError, ServiceIdentityError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)
    click.echo(f"OpenCode version: {report.observed_version}")
    for observation in report.observations:
        click.echo(
            f"{observation.row.value}: {classify_row(observation).value} ({observation.state.value}) — "
            f"{observation.summary}"
        )
    click.echo(f"compatibility: {report.classification().value}")
    if not report.admissible():
        raise click.exceptions.Exit(1)


def _isolated_version(children: IChildProcesses, binary: Path) -> str:
    """The binary's raw ``--version`` output, read before either proof is chosen: in a scratch directory with a
    disposable home and empty XDG roots, so no binary touches the operator's OpenCode state until its proof runs."""
    with tempfile.TemporaryDirectory(prefix="blizzard-opencode-version-") as isolated:
        root = Path(isolated)
        roots = {name: root / name.lower() for name in ("HOME", *_OPENCODE_DIRECTORY_VARIABLES)}
        for directory in roots.values():
            directory.mkdir(mode=0o700)
        env = AllowlistedEnv.of(()).tool().adding({name: str(path) for name, path in roots.items()})
        return children.run([str(binary), "--version"], cwd=roots["HOME"], env=env, timeout=30).stdout


def _registration(path: Path | None, passwords: set[str]) -> ServiceRegistration | None:
    """The registration now, its password remembered so every service lifetime the run saw is redacted."""
    registration = read_registration(path) if path is not None else None
    if registration is not None:
        passwords.add(registration.password)
    return registration


@opencode_group.command("drive")
@click.option(
    "--request",
    "request_path",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="The turn's request file, written by the runner; read once, then removed.",
)
def opencode_drive(request_path: Path) -> None:
    """Worker: drive one OpenCode 2 turn on the operator's managed service and print its result.

    The worker entrypoint the OpenCode 2 binding launches for each spawn, resume, judgement, and nudge, not meant to
    be run by hand; its exit ends the turn. SIGINT interrupts the session tree and ends the turn interrupted."""
    try:
        request = take_request(request_path)
    except DriveRequestError as exc:
        raise click.ClickException(str(exc)) from exc
    environment = dict(os.environ)
    path = registration_path(environment)
    try:
        core = DriverCore(lambda: read_registration(path) if path is not None else None, UrllibServiceTransport())
    except BIND_FAULTS as exc:
        error = TurnError(ErrorKind.SERVICE, f"no OpenCode service to drive the turn on: {exc}")
        ResultWriter(sys.stdout, request.session_id).finish(Outcome.FAILED, "", (), error)
        return
    stop = threading.Event()
    driver = TurnDriver(core, request, environment, sys.stdout, heartbeat=send_heartbeat, stop=stop)

    def interrupt(signum: int, frame: object) -> None:
        del signum, frame
        stop.set()
        driver.wake.set()

    signal.signal(signal.SIGINT, interrupt)
    driver.run()


def _model_reference(ctx: click.Context, param: click.Parameter, value: str | None) -> ModelReference | None:
    del ctx, param
    if value is None:
        return None
    try:
        return parse_model_option(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc


@opencode_group.command("attend")
@click.argument("session_id")
@click.option(
    "--model",
    "model",
    callback=_model_reference,
    help="The lease's model as provider/model[#variant]; the session is switched to it when it runs another.",
)
@click.option(
    "--opencode",
    "binary",
    default=DEFAULT_OPENCODE_BINARY,
    show_default=True,
    help="The OpenCode 2 executable that opens the session.",
)
def opencode_attend(session_id: str, model: ModelReference | None, binary: str) -> None:
    """Take over a fleet OpenCode 2 session in the operator's own OpenCode.

    Interrupts the session's tree, best effort, replaces its rules with the attended set, switches it to --model when
    it runs another, then runs the --opencode executable on it. Refuses a session no blizzard runner minted. The
    runner's takeover composes this; a later driven resume re-applies the unattended rules and the lease's agent."""
    path = registration_path(os.environ)
    try:
        core = DriverCore(lambda: read_registration(path) if path is not None else None, UrllibServiceTransport())
    except BIND_FAULTS as exc:
        raise click.ClickException(f"no OpenCode service holds the session: {exc}") from exc
    try:
        SessionHandover(core, warn=partial(click.echo, err=True)).prepare(session_id, model)
    except AttendError as exc:
        raise click.ClickException(str(exc)) from exc
    argv = attended_argv(binary, session_id)
    try:
        os.execvp(argv[0], argv)
    except OSError as exc:
        raise click.ClickException(f"cannot run `{binary}`: {exc}") from exc


__all__ = ["opencode_attend", "opencode_compatibility", "opencode_drive", "opencode_group"]
