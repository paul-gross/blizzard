"""The CLI surface contract — golden corpus equality (unit tier, blizzard:cli-contract).

``contracts/cli/`` pins every command node under both ``blizzard hub`` and ``blizzard
runner`` — its full path, help text, short help, and its parameters' spellings, kinds,
types, and required/hidden flags, in declaration order. This is the CLI's own decomposing
guard, ahead of the by-concept package split (blizzard#cli-by-concept): the live tree
below is built the exact same way ``blizzard.tools.cli_surface`` builds it for
recording, so the two can only diverge when the rendered surface itself changes."""

from __future__ import annotations

import json
from typing import Any

import click
import pytest
from typing_extensions import TypeIs

from blizzard.tools.cli_surface import ROOTS, build
from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_CONTRACTS_DIR = repo_root() / "contracts" / "cli"


def _committed(name: str) -> dict[str, Any]:
    return json.loads((_CONTRACTS_DIR / f"{name}.json").read_text())


def _string_keyed(node: object) -> TypeIs[dict[str, object]]:
    return isinstance(node, dict)


def _listed(node: object) -> TypeIs[list[object]]:
    return isinstance(node, list)


def _diff(path: str, live: object, committed: object) -> list[str]:
    """Every leaf mismatch between a live node and its committed counterpart, named by
    the command path it was found under — so a failure points at one node, not the
    whole tree."""
    if _string_keyed(live) and _string_keyed(committed):
        offenders: list[str] = []
        for key in sorted(set(live) | set(committed)):
            if key not in live:
                offenders.append(f"{path}: committed has {key!r} the live tree does not")
            elif key not in committed:
                offenders.append(f"{path}: live has {key!r} the committed snapshot does not")
            else:
                offenders.extend(_diff(f"{path}.{key}" if key != "commands" else path, live[key], committed[key]))
        return offenders
    if _listed(live) and _listed(committed):
        if len(live) != len(committed):
            return [f"{path}: {len(live)} live entries vs {len(committed)} committed"]
        offenders = []
        for index, (live_item, committed_item) in enumerate(zip(live, committed, strict=True)):
            offenders.extend(_diff(f"{path}[{index}]", live_item, committed_item))
        return offenders
    if live != committed:
        return [f"{path}: live={live!r} != committed={committed!r}"]
    return []


@pytest.mark.parametrize("name,root", ROOTS, ids=[name for name, _ in ROOTS])
def test_live_tree_matches_the_committed_surface_contract(name: str, root: click.Group) -> None:
    live = build(name, root)
    committed = _committed(name)
    offenders = _diff(name, live, committed)
    assert not offenders, "\n".join(
        [
            f"the live `blizzard {name}` CLI surface has drifted from contracts/cli/{name}.json:",
            *offenders,
            "regenerate with `uv run python -m blizzard.tools.cli_surface` and commit the result "
            "if the drift is intended.",
        ]
    )


def test_every_root_is_scanned() -> None:
    """A renamed or dropped root would otherwise reduce this guard to a green no-op."""
    assert {name for name, _ in ROOTS} == {"hub", "runner"}


def test_login_and_logout_keep_session_service_off_the_recorded_surface() -> None:
    """``session_service`` reaches ``login``/``logout`` through ``ctx.params``, never as
    a declared click option — the recorded-surface half of ``SessionServiceCommand``'s
    decision (``src/blizzard/hub/cli/command.py``)."""
    hub_tree = build("hub", dict(ROOTS)["hub"])
    for name in ("login", "logout"):
        param_names = {p["name"] for p in hub_tree["commands"][name]["params"]}
        assert "session_service" not in param_names, f"{name} declared session_service as a recorded parameter"


def test_a_deprecated_command_and_option_are_recorded_and_others_carry_no_key() -> None:
    @click.group()
    def root() -> None: ...

    @root.command(deprecated=True)
    @click.option("--old", deprecated=True)
    @click.option("--new")
    def gone(old: str, new: str) -> None: ...

    @root.command()
    def kept() -> None: ...

    shape = build("root", root)
    assert shape["commands"]["gone"]["deprecated"] is True
    assert "deprecated" not in shape["commands"]["kept"]
    params = {p["name"]: p for p in shape["commands"]["gone"]["params"]}
    assert params["old"]["deprecated"] is True
    assert "deprecated" not in params["new"]
