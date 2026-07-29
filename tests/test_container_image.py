"""The hub container image (``packaging/docker/``) — the static packaging contract
(issue #188), modeled on ``tests/test_systemd_units.py``. No docker required — this
is the docker-free static guard that catches packaging rot even on a machine with
no docker at all, running in the default ``blizzard:unit-test`` tier. The image
actually building, booting, and serving is ``blizzard:image-smoke``
(``mise run image-smoke``), local-only.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DOCKER_DIR = _REPO_ROOT / "packaging" / "docker"


def _dockerfile() -> str:
    return (_DOCKER_DIR / "Dockerfile").read_text()


def _entrypoint() -> str:
    return (_DOCKER_DIR / "entrypoint.sh").read_text()


def test_dockerfile_and_entrypoint_exist() -> None:
    assert (_DOCKER_DIR / "Dockerfile").is_file()
    assert (_DOCKER_DIR / "entrypoint.sh").is_file()


def test_image_installs_git() -> None:
    text = _dockerfile()
    match = re.search(r"apt-get install.*?(?=\n\n|\nRUN|\nUSER|\nCOPY)", text, re.DOTALL)
    assert match, "Dockerfile has no apt-get install step"
    assert re.search(r"\bgit\b", match.group(0)), "the apt-get install step must include git"


def test_image_runs_as_a_non_root_user() -> None:
    text = _dockerfile()
    user_lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("USER ")]
    assert user_lines, "Dockerfile declares no USER — the container would run as root"
    user = user_lines[-1].split()[1]
    assert user not in ("root", "0"), f"Dockerfile's last USER directive is {user!r} — must be non-root"


def test_a_system_user_owns_the_runtime_dir() -> None:
    text = _dockerfile()
    assert "useradd" in text, "Dockerfile must create the non-root user it USERs into"
    assert "chown" in text and "/var/lib/blizzard" in text, "the runtime dir must be owned by that user"


def test_postgres_extra_is_installed_from_the_built_wheel() -> None:
    text = _dockerfile()
    assert re.search(r"pip install.*blizzard-\*\.whl.*\[postgres\]", text), (
        "the image must install the wheel's `postgres` extra (bzh:sql-portable — the "
        "driver makes the store URL a real choice, not just a declared one)"
    )


def test_env_defaults_are_declared() -> None:
    text = _dockerfile()
    assert re.search(r"\bBZ_HUB_HOST=0\.0\.0\.0\b", text)
    assert re.search(r"\bBZ_LOG_FORMAT=json\b", text)
    assert re.search(r"\bBZ_HUB_DIR=/var/lib/blizzard/hub\b", text)


def test_healthcheck_hits_the_health_endpoint_with_no_curl() -> None:
    text = _dockerfile()
    assert "HEALTHCHECK" in text
    healthcheck_block = text[text.index("HEALTHCHECK") :]
    assert "/api/health" in healthcheck_block
    assert "curl" not in healthcheck_block, "no curl in the base image — use the stdlib request"


def test_entrypoint_scaffolds_then_migrates_then_execs_host() -> None:
    text = _entrypoint()
    init_idx = text.index("blizzard-hub init")
    migrate_idx = text.index("blizzard-hub migrate")
    host_idx = text.index("blizzard-hub host")
    assert init_idx < migrate_idx < host_idx, "entrypoint must scaffold (if absent), then migrate, then host"
    host_line = next(ln for ln in text.splitlines() if "blizzard-hub host" in ln)
    assert host_line.strip().startswith("exec "), "the host step must be `exec`'d — the entrypoint becomes the daemon"


def test_entrypoint_scaffolds_only_when_the_config_file_is_absent() -> None:
    """An unconditional `init` would migrate twice (once inside `init`, once via the
    entrypoint's own explicit `migrate` step) and blur the scaffold->migrate->host
    ordering this file exists to keep literal (plan decision 2)."""
    text = _entrypoint()
    assert re.search(r"if\s+\[\s*!\s*-f\s+\S*blizzard-hub\.toml\S*\s*\]", text), (
        "the `init` call must be behind a conditional on the config file's absence"
    )


def test_daemon_startup_path_carries_no_migrate_call() -> None:
    """``hub host``'s CLI command must never call migrate itself — the entrypoint (or
    the systemd unit's ``ExecStartPre``, ``tests/test_systemd_units.py``) owns that
    step. The daemon refuses to start on a revision mismatch instead
    (``ensure_current_revision``); folding migrate into the host path would let a
    concurrent instance race a migration against a daemon that just started serving.
    """
    cli_text = (_REPO_ROOT / "src" / "blizzard" / "hub" / "cli.py").read_text()
    match = re.search(r"\ndef host\(.*?(?=\n@|\Z|\ndef )", cli_text, re.DOTALL)
    assert match, "could not locate hub cli's host() command body"
    assert "migrate" not in match.group(0), "hub host's CLI command must not call migrate itself"


def test_readme_documents_the_mount_and_env_vars() -> None:
    readme = (_DOCKER_DIR / "README.md").read_text()
    assert "/var/lib/blizzard/hub" in readme
    for var in ("BZ_HUB_DB_URL", "BZ_HUB_HOST", "BZ_HUB_PORT", "BZ_HUB_DIR"):
        assert var in readme, f"packaging/docker/README.md must document {var}"
