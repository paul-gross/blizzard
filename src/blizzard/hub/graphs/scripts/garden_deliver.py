"""The garden delivery node's own script (blizzard#393) — posts a routine run's
``--delta``/``--proposals`` artifact names to the hub's garden-delivery route and reports
the outcome. Pure stdlib (``bzh:deterministic-shell``), built on `land_common`'s own
:class:`~blizzard.hub.graphs.scripts.land_common.ScriptEnv`/
:func:`~blizzard.hub.graphs.scripts.land_common.deliver_and_report` primitives rather
than duplicating them."""

from __future__ import annotations

import argparse
import sys

from blizzard.hub.graphs.scripts import land_common
from blizzard.hub.graphs.scripts.land_common import MarkerWriteError, ScriptEnv

_ENV_CHUNK_ID = "BZ_HUB_CHUNK_ID"
_ENV_NODE_ID = "BZ_HUB_NODE_ID"
_ENV_EPOCH = "BZ_HUB_EPOCH"
_ENV_GARDEN_DELIVERY_URL = "BZ_HUB_GARDEN_DELIVERY_URL"
_ENV_MARKER_TOKEN = "BZ_HUB_MARKER_TOKEN"

# The failure-marker name a rejected delivery's `invalid` edge reads back (see
# `garden-routine/prompts/reconcile.from-deliver.md`).
_FAILURE_MARKER_NAME = "garden-delivery-failure"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """``--delta``/``--proposals`` each repeat (D3): ``garden_deliver --delta a --delta b
    --proposals docket``."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--delta", action="append", default=[])
    parser.add_argument("--proposals", action="append", default=[])
    return parser.parse_args(argv)


def main() -> int:
    """Run the delivery, aborting cleanly on an unconfirmed failure-marker write."""
    try:
        return _deliver()
    except MarkerWriteError as exc:
        print(f"marker write failed: {exc}", file=sys.stderr)
        return 1


def _deliver() -> int:
    args = _parse_args(sys.argv[1:])
    env = ScriptEnv()
    # Required vars are read in table order, so the first one missing is the one named.
    env.require(_ENV_CHUNK_ID)
    env.require(_ENV_NODE_ID)
    env.require(_ENV_EPOCH)
    delivery_url = env.require(_ENV_GARDEN_DELIVERY_URL)
    token = env.require(_ENV_MARKER_TOKEN)

    return land_common.deliver_and_report(
        url=delivery_url,
        body={"delta": args.delta, "proposals": args.proposals},
        token=token,
        env=env,
        failure_marker_name=_FAILURE_MARKER_NAME,
        action="garden delivery",
    )


if __name__ == "__main__":
    sys.exit(main())
