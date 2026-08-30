"""The CLI surface contract — golden corpus equality (unit tier, blizzard:cli-contract).

``contracts/cli/`` pins every command node under both ``blizzard hub`` and ``blizzard
runner`` — its full path, help text, short help, and its parameters' spellings, kinds,
types, and required/hidden flags, in declaration order. This is the CLI's own decomposing
guard, ahead of the by-concept package split (blizzard#cli-by-concept): the live tree
below is built the exact same way ``blizzard.tools.cli_surface`` builds it for
recording, so the two can only diverge when the rendered surface itself changes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from blizzard.tools.cli_surface import ROOTS, build

pytestmark = pytest.mark.unit

_CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "contracts" / "cli"


def _committed(name: str) -> dict[str, Any]:
    return json.loads((_CONTRACTS_DIR / f"{name}.json").read_text())


def _diff(path: str, live: Any, committed: Any) -> list[str]:
    """Every leaf mismatch between a live node and its committed counterpart, named by
    the command path it was found under — so a failure points at one node, not the
    whole tree."""
    if isinstance(live, dict) and isinstance(committed, dict):
        offenders: list[str] = []
        for key in sorted(set(live) | set(committed)):
            if key not in live:
                offenders.append(f"{path}: committed has {key!r} the live tree does not")
            elif key not in committed:
                offenders.append(f"{path}: live has {key!r} the committed snapshot does not")
            else:
                offenders.extend(_diff(f"{path}.{key}" if key != "commands" else path, live[key], committed[key]))
        return offenders
    if isinstance(live, list) and isinstance(committed, list):
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
def test_live_tree_matches_the_committed_surface_contract(name: str, root: object) -> None:
    live = build(name, root)  # type: ignore[arg-type]
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
