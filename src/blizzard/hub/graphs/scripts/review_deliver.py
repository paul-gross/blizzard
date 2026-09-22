"""The `record-findings` node's own script (blizzard#582) — posts to the hub's
review-findings-delivery route and reports the outcome. The route reads the chunk's own
newest `review-finding-delta` artifact server-side, so this script carries no body — the
`garden_deliver.py` shape, minus the `--delta`/`--proposals` seam that script needs and
this route does not. Pure stdlib (`bzh:deterministic-shell`), built on `land_common`'s own
:class:`~blizzard.hub.graphs.scripts.land_common.ScriptEnv`/``forge_request``/
:class:`~blizzard.hub.graphs.scripts.land_common.MarkerWriter` primitives rather than
duplicating them."""

from __future__ import annotations

import sys

from blizzard.hub.graphs.scripts import land_common
from blizzard.hub.graphs.scripts.land_common import MarkerWriteError, MarkerWriter

# The mid-run marker callback's token header (issue #230) — restated rather than imported,
# mirroring `land_common`, to keep this module's dependency on that one at its seam only.
_MARKER_TOKEN_HEADER = "X-Blizzard-Marker-Token"

_ENV_REVIEW_FINDINGS_URL = "BZ_HUB_REVIEW_FINDINGS_URL"
_ENV_MARKER_TOKEN = "BZ_HUB_MARKER_TOKEN"
_ENV_MARKER_CALLBACK_URL = "BZ_HUB_MARKER_CALLBACK_URL"

# The failure-marker name a rejected delivery's `invalid` edge reads back (see
# `advanced-development-workflow/prompts/retrospective.from-record-findings.md` and its
# sibling copies in the other two lanes).
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

    status, body = land_common.forge_request(
        "POST",
        delivery_url,
        token=None,
        body=None,
        headers={_MARKER_TOKEN_HEADER: token},
    )
    if not (200 <= status < 300):
        # A fault in the POST itself is fatal, never printed over as a `recorded`/`invalid`
        # outcome — no printed success over an unwritten delivery.
        print(f"review findings delivery request failed: HTTP {status} {body!r}", file=sys.stderr)
        return 1

    outcome = (body or {}).get("outcome")
    detail = (body or {}).get("detail", "")
    if outcome == "recorded":
        print("recorded")
        return 0
    if outcome == "invalid":
        print(f"review findings delivery rejected: {detail}", file=sys.stderr)
        markers = MarkerWriter(
            callback_url=env.get(_ENV_MARKER_CALLBACK_URL), token=token, request=land_common.forge_request
        )
        markers.post(_FAILURE_MARKER_NAME, detail)
        print("invalid")
        return 0

    print(f"review findings delivery returned an unrecognized outcome: {outcome!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
