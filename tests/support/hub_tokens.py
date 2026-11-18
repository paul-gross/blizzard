"""A host's sealed runner hub tokens under its own key, for tests that seal, replace or read a runner's token row."""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.composition import build_hub_tokens
from blizzard.runner.hub.sealed_tokens import HubTokens


@contextmanager
def host_hub_tokens(root: Path, db_url: str) -> Generator[HubTokens]:
    """The host at ``root``'s tokens over its migrated store ``db_url``, minting its key when it holds none."""
    engine = create_engine_from_url(db_url)
    try:
        tokens = build_hub_tokens(engine, root, environ=os.environ, clock=SystemClock(), mint=True)
        assert tokens is not None
        yield tokens
    finally:
        engine.dispose()
