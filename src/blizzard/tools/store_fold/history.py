"""A floor release's own store, built by the release's own ``migrate`` and read out by its own interpreter."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

from blizzard.tools.release_install import Installation
from blizzard.tools.store_fold.tree import FoldError

_RENDERER = Path(__file__).with_name("render_floor.py")
_HUB_DB_VAR = "BZ_HUB_DB_URL"


def _unanswered_hub_url() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{sock.getsockname()[1]}"


def _checked(floor: Installation, what: str, done: Any) -> None:
    if done.returncode != 0:
        raise FoldError(f"{floor.label} {what} failed:\n{done.stdout}\n{done.stderr}")


def history_store(floor: Installation, tree: str, directory: Path, *, db_url: str | None = None) -> str:
    """The URL of ``tree``'s store, built from empty by ``floor``'s own history under ``directory``.

    A runner store is built by ``runner init`` against a hub nothing answers at, which writes the directory and a
    migrated store and registers nothing. ``db_url`` names an external Postgres database for the hub tree."""
    if tree == "hub":
        flags = ["--allow-external-db"] if db_url else []
        env = floor.env({_HUB_DB_VAR: db_url} if db_url else None)
        _checked(floor, "hub init", floor.run("blizzard-hub", "init", str(directory), *flags, env=env))
        _checked(floor, "hub migrate", floor.run("blizzard-hub", "migrate", "--dir", str(directory), *flags, env=env))
        return db_url or f"sqlite:///{directory / 'data' / 'hub.db'}"
    if db_url:
        raise FoldError("the runner history cannot be built on an external database")
    floor.run("blizzard-runner", "init", str(directory), "--hub", _unanswered_hub_url(), env=floor.env())
    _checked(floor, "runner migrate", floor.run("blizzard-runner", "migrate", "--dir", str(directory)))
    return f"sqlite:///{directory / 'data' / 'host.db'}"


def render(floor: Installation, tree: str, store_url: str) -> dict[str, Any]:
    """What the floor-side renderer prints when ``floor``'s interpreter runs it over ``tree`` and its history store."""
    done = floor.run("python", "-I", str(_RENDERER), tree, store_url, env=floor.env())
    _checked(floor, f"{tree} render", done)
    document: dict[str, Any] = json.loads(done.stdout)
    return document
