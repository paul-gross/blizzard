"""The `record-findings` node's own script (blizzard#582) — posts to the hub's
review-findings-delivery route and reports the outcome. The route reads the chunk's own
newest `review-finding-delta` artifact server-side, so this script carries no body. Pure
stdlib (`bzh:deterministic-shell`), built on `land_common`'s own `ScriptEnv`/
`deliver_and_report` primitives rather than duplicating them."""

from __future__ import annotations

import sys

from blizzard.hub.graphs.scripts import land_common
from blizzard.hub.graphs.scripts.land_common import MarkerWriteError

_ENV_REVIEW_FINDINGS_URL = "BZ_HUB_REVIEW_FINDINGS_URL"
_ENV_MARKER_TOKEN = "BZ_HUB_MARKER_TOKEN"

# The failure-marker name a rejected delivery's `invalid` edge reads back.
_FAILURE_MARKER_NAME = "review-findings-failure"


def main() -> int:
    """Run the delivery, aborting cleanly on an unconfirmed failure-marker write."""
    try:
        return _deliver()
    except MarkerWriteError as exc:
        print(f"marker write failed: {exc}", file=sys.stderr)
        return 1


def _deliver() -> int:
    env = land_common.ScriptEnv()
    delivery_url = env.require(_ENV_REVIEW_FINDINGS_URL)
    token = env.require(_ENV_MARKER_TOKEN)

    return land_common.deliver_and_report(
        url=delivery_url,
        body=None,
        token=token,
        env=env,
        failure_marker_name=_FAILURE_MARKER_NAME,
        action="review findings delivery",
    )


if __name__ == "__main__":
    sys.exit(main())
