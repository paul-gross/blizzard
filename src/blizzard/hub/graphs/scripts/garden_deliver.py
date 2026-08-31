"""The garden delivery node's own script (blizzard#393 Phase 4) — posts a routine run's
``--delta``/``--proposals`` artifact names to the hub's garden-delivery route and reports
the outcome. Pure stdlib (``bzh:deterministic-shell``), built on `land_common`'s own
:class:`~blizzard.hub.graphs.scripts.land_common.ScriptEnv`/``forge_request``/
:class:`~blizzard.hub.graphs.scripts.land_common.MarkerWriter` primitives rather than
duplicating them."""

from __future__ import annotations

import argparse
import sys

from blizzard.hub.graphs.scripts import land_common
from blizzard.hub.graphs.scripts.land_common import MarkerWriteError, MarkerWriter, ScriptEnv

# The mid-run marker callback's token header (issue #230) — restated rather than imported,
# mirroring `land_common`, to keep this module's dependency on that one at its seam only.
_MARKER_TOKEN_HEADER = "X-Blizzard-Marker-Token"

_ENV_CHUNK_ID = "BZ_HUB_CHUNK_ID"
_ENV_NODE_ID = "BZ_HUB_NODE_ID"
_ENV_EPOCH = "BZ_HUB_EPOCH"
_ENV_GARDEN_DELIVERY_URL = "BZ_HUB_GARDEN_DELIVERY_URL"
_ENV_MARKER_TOKEN = "BZ_HUB_MARKER_TOKEN"
_ENV_MARKER_CALLBACK_URL = "BZ_HUB_MARKER_CALLBACK_URL"

# The failure-marker name a rejected delivery's `invalid` edge reads back (the reviewed
# plan's own Phase 4 §The route and the script).
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

    status, body = land_common.forge_request(
        "POST",
        delivery_url,
        token=None,
        body={"delta": args.delta, "proposals": args.proposals},
        headers={_MARKER_TOKEN_HEADER: token},
    )
    if not (200 <= status < 300):
        # A fault in the POST itself is fatal, never printed over as a `recorded`/`invalid`
        # outcome — no printed success over an unwritten delivery.
        print(f"garden delivery request failed: HTTP {status} {body!r}", file=sys.stderr)
        return 1

    outcome = (body or {}).get("outcome")
    detail = (body or {}).get("detail", "")
    if outcome == "recorded":
        print("recorded")
        return 0
    if outcome == "invalid":
        print(f"garden delivery rejected: {detail}", file=sys.stderr)
        markers = MarkerWriter(
            callback_url=env.get(_ENV_MARKER_CALLBACK_URL), token=token, request=land_common.forge_request
        )
        markers.post(_FAILURE_MARKER_NAME, detail)
        print("invalid")
        return 0

    print(f"garden delivery returned an unrecognized outcome: {outcome!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
