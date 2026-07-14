"""The colocated systemd units (``packaging/systemd/``) — the boot-recovery contract.

The MVP journey has the machine reboot and "the supervisor and the colocated hub
[come] back under systemd" (product/mvp.md). Two mechanisms deliver that: the units
enable at boot (``WantedBy=multi-user.target``) and restart on a crash (``Restart=``);
the daemons' own startup pass (runner REAP, hub idempotent re-flush) does the rest,
proven by the whole-process crash-sweep cases (docs/deployment.md).

This unit test holds the *packaging* half of that contract so it cannot silently rot:
each shipped ``.service`` file must launch a real, packaged entry point via ``host``,
reconcile the schema before it (D-099), and carry the restart + boot-enable directives
the recovery contract depends on. The *behavior* is the crash sweep's job; this is the
static-asset guard, so it needs no systemd installed and runs in the default tier.
"""

from __future__ import annotations

import configparser
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
    """ExecStartPre migrates the store (D-099) so a wheel upgrade + reboot self-heals."""
    entry_point, runtime_dir = _UNITS[name]
    pre = _parse_unit(name).get("Service", "ExecStartPre")
    argv = pre.split()
    assert Path(argv[0]).name == entry_point, f"{name} ExecStartPre must run {entry_point}"
    assert "migrate" in argv, f"{name} ExecStartPre must `migrate` before host (D-099)"
    assert runtime_dir in argv, f"{name} ExecStartPre must target {runtime_dir}"


@pytest.mark.parametrize("name", sorted(_UNITS))
def test_restart_and_boot_enable_directives_are_present(name: str) -> None:
    """The two mechanisms the reboot-recovery contract depends on (docs/deployment.md)."""
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


def test_no_forge_or_pm_credentials_are_configured_on_the_runner_unit() -> None:
    """Credentials live only at the hub (D-047/D-084) — the runner unit must not carry them."""
    runner_text = (_SYSTEMD_DIR / "blizzard-runner.service").read_text()
    for env_file_line in [ln for ln in runner_text.splitlines() if ln.startswith("EnvironmentFile")]:
        assert "runner.env" in env_file_line, f"runner unit points at a non-runner env file: {env_file_line}"
    assert "BZ_FORGE_TOKEN" not in runner_text, "the runner unit must not reference a forge token"
