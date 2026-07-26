"""The runner-local work-item pass-through proxy — ``GET /api/chunks/{id}/work-items``.

A build worker reads its chunk's work items — each pointer's issue body and comment thread —
through this proxy while it works the build node (``graphs/default/prompts/build.md``): the runner
**forwards** the read to the hub's pass-through route, and the hub calls the vendor with
its own credentials. The layering is the point: a worker never talks to the hub
or the work source directly, and work-source credentials never reach the runner. Contents are never
stored anywhere on the path — the pointer is the durable referent, the item is fetched
fresh each call.

Read-only over its wiring (``bzh:controller-read-only``): it forwards to the hub URL the
``host`` composition root resolved onto ``app.state.config``. ``httpx`` is used only to
reach the hub — the same outbound-only edge the reconciliation loop's hub client rides;
a transport failure to the hub is a ``502`` and the hub's own status (``404``
unknown chunk, ``503`` no work-source configured) passes through verbatim so the worker
sees the real reason. A per-pointer forge failure is not a status — the hub degrades it to
an ``error`` on that entry, so the worker still reads the pointers it did reach.

The forward carries the same ``Authorization: Bearer`` credential as the reconciliation
loop's own hub client (issue #86b) — ``config.hub_token``, resolved once at ``host``
startup — rather than a separately patched header: one credential path for every
runner->hub call. No header at all when ``hub_token`` is empty (unenrolled / warn-mode
fleet with no token installed yet).

The forward targets the hub's fleet-side counterpart (``/api/fleet/chunks/{id}/work-items``,
issue #87), not the board's own anonymous ``/api/chunks/{id}/work-items`` — a runner
bearer token is confined to the fleet router, so forwarding it to the operator path
would now be rejected under ``enforce``. Both routes render the same read; the board
reaches its own copy directly, unauthenticated.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.logging import get_logger
from blizzard.runner.config import RunnerConfig
from blizzard.wire.chunk import WorkItemsView

router = APIRouter(prefix="/api", tags=["runner"])

_log = get_logger("blizzard.runner.api.work_items")
_HUB_TIMEOUT = 15.0


@router.get("/chunks/{chunk_id}/work-items", response_model=WorkItemsView)
def get_work_items(chunk_id: str, request: Request) -> WorkItemsView:
    """Forward a chunk's work-items read to the hub — the layered pass-through."""
    config: RunnerConfig | None = getattr(request.app.state, "config", None)
    if config is None or not config.hub_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner not wired to a hub — start via `blizzard runner host`",
        )
    url = f"{config.hub_url.rstrip('/')}/api/fleet/chunks/{chunk_id}/work-items"
    try:
        upstream = httpx.get(url, headers=config.auth_headers(), timeout=_HUB_TIMEOUT)
    except httpx.HTTPError as exc:
        _log.error("work-items proxy could not reach the hub", chunk_id=chunk_id, error=str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"hub unreachable: {exc}") from exc
    if upstream.status_code != status.HTTP_200_OK:
        # Surface the hub's status verbatim — 404 (unknown chunk) or 503 (no work-source
        # configured) — so the worker reads the real reason.
        raise HTTPException(status_code=upstream.status_code, detail=_upstream_detail(upstream))
    return WorkItemsView.model_validate(upstream.json())


# The runner's half of the issue-#55 alias (see :mod:`blizzard.hub.api.chunks` for the
# rationale — out-of-tree tooling, not intra-fleet version skew). The *forward* is
# unconditionally to the hub's canonical `/work-items`: a runner and its hub are one
# wheel (`docs/deployment.md`), so there is no older hub on the other side of this call.
#
# Note this alias is **not** what keeps a pre-rename graph prompt working: such a worker
# invokes the CLI, not this route, and `blizzard runner pm-items` is the alias that
# catches it (`runner/cli.py`). This one is here so the two daemons present the same
# path surface to an external caller.
router.add_api_route(
    "/chunks/{chunk_id}/pm-items",
    get_work_items,
    methods=["GET"],
    response_model=WorkItemsView,
    deprecated=True,
    name="runner_get_pm_items_deprecated_alias",
    summary="Deprecated alias for GET /chunks/{chunk_id}/work-items",
    description=(
        "Deprecated since issue #55 — use `GET /chunks/{chunk_id}/work-items`, which this "
        "path aliases onto the identical handler and returns the identical view."
    ),
)


def _upstream_detail(response: httpx.Response) -> str:
    """The hub's error detail, unwrapped from its JSON body when present."""
    try:
        payload = response.json()
    except ValueError:
        return response.text
    if isinstance(payload, dict) and "detail" in payload:
        return str(payload["detail"])
    return response.text
