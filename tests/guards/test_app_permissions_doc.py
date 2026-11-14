"""The GitHub App permissions the operator docs list are the ones the hub mints with (unit tier).

``docs/deployment/credentials.md``'s "App permissions" table names, per scope, each permission an installation token is
minted with, its access, and what it is needed for: ``required``, or the capability withheld without it. It must equal
``APP_REQUIRED_PERMISSIONS`` and ``APP_OPTIONAL_PERMISSIONS``, so an operator granting what the page says grants what
the hub asks for."""

from __future__ import annotations

import re

from blizzard.hub.forge.internal.github_auth import APP_OPTIONAL_PERMISSIONS, APP_REQUIRED_PERMISSIONS
from tests.support.repo_files import repo_root

_DOC = repo_root() / "docs" / "deployment" / "credentials.md"
_HEADING = "### App permissions"
_CELL = re.compile(r"^`([^`]+)`$")


def _documented() -> set[tuple[str, str, str, str]]:
    lines = _DOC.read_text(encoding="utf-8").splitlines()
    start = lines.index(_HEADING) + 1
    rows: set[tuple[str, str, str, str]] = set()
    in_table = False
    for line in lines[start:]:
        if line.startswith("#"):
            break
        if not line.startswith("|"):
            if in_table:
                break
            continue
        in_table = True
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells[0] == "Scope" or set(cells[0]) <= {"-"}:
            continue
        scope, permission, access, needed = (_CELL.sub(r"\1", cell) for cell in cells)
        rows.add((scope, permission, access, needed))
    return rows


def _minted() -> set[tuple[str, str, str, str]]:
    rows = {
        (str(scope), permission, access, "required")
        for scope, permissions in APP_REQUIRED_PERMISSIONS.items()
        for permission, access in permissions.items()
    }
    rows |= {
        (str(scope), permission, access, str(capability))
        for scope, permissions in APP_OPTIONAL_PERMISSIONS.items()
        for permission, (access, capability) in permissions.items()
    }
    return rows


def test_the_documented_app_permissions_are_the_minted_ones() -> None:
    documented = _documented()

    assert documented, f"{_DOC} has no table under {_HEADING!r}"
    assert documented == _minted()
