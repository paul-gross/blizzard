"""The colocated systemd units (``packaging/systemd/``) — the boot-recovery contract.

Each shipped ``.service`` file must launch a real, packaged entry point via ``host``,
reconcile the schema before it, and carry the restart + boot-enable directives the
recovery contract depends on — the *behavior* is the crash sweep's job."""

from __future__ import annotations

import ast
import configparser
import re
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SYSTEMD_DIR = _REPO_ROOT / "packaging" / "systemd"

# unit file -> (daemon entry-point, the runtime dir the colocated install uses).
_UNITS = {
    "blizzard-hub.service": ("blizzard-hub", "/var/lib/blizzard/hub"),
    "blizzard-runner.service": ("blizzard-runner", "/var/lib/blizzard/runner"),
}


def _packaged_scripts() -> dict[str, str]:
    """The console-script entry points the wheel actually ships (pyproject [project.scripts])."""
    data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text())
    return data["project"]["scripts"]


class _CaseSensitiveParser(configparser.ConfigParser):
    # systemd keys are case-sensitive (ExecStart, WantedBy); the default lower-cases them.
    def optionxform(self, optionstr: str) -> str:
        return optionstr


def _parse_unit(name: str) -> configparser.ConfigParser:
    # systemd unit files are INI-shaped but allow repeated keys (e.g. ExecStartPre)
    # and use no ConfigParser interpolation; parse leniently.
    parser = _CaseSensitiveParser(strict=False, interpolation=None)
    parser.read(_SYSTEMD_DIR / name)
    return parser


@pytest.mark.parametrize("name", sorted(_UNITS))
def test_unit_has_the_standard_service_sections(name: str) -> None:
    parser = _parse_unit(name)
    for section in ("Unit", "Service", "Install"):
        assert parser.has_section(section), f"{name} is missing its [{section}] section"


@pytest.mark.parametrize("name", sorted(_UNITS))
def test_execstart_launches_a_packaged_entry_point_as_host(name: str) -> None:
    """ExecStart must invoke a real shipped console script and *become* the daemon (``host``)."""
    entry_point, runtime_dir = _UNITS[name]
    scripts = _packaged_scripts()
    assert entry_point in scripts, f"{name} launches {entry_point}, not a [project.scripts] entry point"

    exec_start = _parse_unit(name).get("Service", "ExecStart")
    argv = exec_start.split()
    assert Path(argv[0]).name == entry_point, f"{name} ExecStart runs {argv[0]}, not {entry_point}"
    assert argv[0].startswith("/"), "systemd requires an absolute ExecStart path"
    assert argv[1] == "host", f"{name} ExecStart must `host` the daemon, got {argv[1]!r}"
    assert runtime_dir in argv, f"{name} ExecStart must point --dir at {runtime_dir}"


@pytest.mark.parametrize("name", sorted(_UNITS))
def test_schema_is_reconciled_before_the_daemon_opens_the_store(name: str) -> None:
    """ExecStartPre migrates the store so a wheel upgrade + reboot self-heals."""
    entry_point, runtime_dir = _UNITS[name]
    pre = _parse_unit(name).get("Service", "ExecStartPre")
    argv = pre.split()
    assert Path(argv[0]).name == entry_point, f"{name} ExecStartPre must run {entry_point}"
    assert "migrate" in argv, f"{name} ExecStartPre must `migrate` before host"
    assert runtime_dir in argv, f"{name} ExecStartPre must target {runtime_dir}"


@pytest.mark.parametrize("name", sorted(_UNITS))
def test_restart_and_boot_enable_directives_are_present(name: str) -> None:
    """The two mechanisms the reboot-recovery contract depends on (docs/deployment/recovery.md)."""
    parser = _parse_unit(name)
    # Restart on crash — the "came back under systemd" mechanism for a kill -9.
    restart = parser.get("Service", "Restart")
    assert restart not in ("", "no"), f"{name} must set Restart= (crash recovery); got {restart!r}"
    # Start at boot — the reboot half. `systemctl enable` wires this target in.
    wanted_by = parser.get("Install", "WantedBy")
    assert "multi-user.target" in wanted_by, f"{name} must be WantedBy a boot target; got {wanted_by!r}"


def test_both_colocated_units_ship_and_the_runner_orders_after_the_hub() -> None:
    """The colocated pair: both units exist and the supervisor prefers the hub up first."""
    for name in _UNITS:
        assert (_SYSTEMD_DIR / name).is_file(), f"missing colocated unit {name}"
    after = _parse_unit("blizzard-runner.service").get("Unit", "After")
    assert "blizzard-hub.service" in after, "the runner unit should order After the colocated hub"


def test_no_forge_or_work_source_credentials_are_configured_on_the_runner_unit() -> None:
    """Credentials live only at the hub — the runner unit must not carry them."""
    runner_text = (_SYSTEMD_DIR / "blizzard-runner.service").read_text()
    for env_file_line in [ln for ln in runner_text.splitlines() if ln.startswith("EnvironmentFile")]:
        assert "runner.env" in env_file_line, f"runner unit points at a non-runner env file: {env_file_line}"
    assert "BZ_FORGE_TOKEN" not in runner_text, "the runner unit must not reference a forge token"


_TEST_CITATION = re.compile(r"(tests/[\w/]+\.py)::(test_\w+)")


def _defined_test_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


@pytest.mark.parametrize("name", sorted(_UNITS))
def test_a_cited_recovery_proof_is_a_real_test(name: str) -> None:
    """Each unit's crash-recovery comment names the test that proves it (#295) — a citation
    that outlives the test it named is worse than none, so a rename here must be caught."""
    text = (_SYSTEMD_DIR / name).read_text()
    citations = _TEST_CITATION.findall(text)
    assert citations, f"{name} carries no recovery-proof citation to check"
    for rel_path, test_name in citations:
        cited_file = _REPO_ROOT / rel_path
        assert cited_file.is_file(), f"{name} cites {rel_path}, which does not exist"
        assert test_name in _defined_test_names(cited_file), (
            f"{name} cites {rel_path}::{test_name}, which is not defined there"
        )
