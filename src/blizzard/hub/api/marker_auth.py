"""The marker-write route's own authorization dependency (issue #230, phase 2).

``POST /chunks/{chunk_id}/hub-markers`` (:mod:`blizzard.hub.api.chunks`) is a hub
command node's own mid-run callback, not a human/operator action — a land script
posts here from inside the same process that spawned it
(:mod:`blizzard.hub.delivery.marker_auth`'s module docstring explains the
same-process trust that makes this work). This module adds a **second** way to pass
that route's gate, layered in front of the existing ``require(CHUNK_CONTROL)`` human
gate rather than replacing it: a request carrying a valid, live marker token for the
exact ``(chunk_id, node_id, epoch)`` triple the route's own path/query params name is
granted without a human session at all; anything else (no token, a token for a
different step, a revoked token) falls through to ``require(CHUNK_CONTROL)``'s
existing behavior unchanged — an operator's own session still works under ``oauth``,
and ``auth.mode = "none"`` still grants unconditionally.

The token rides the ``X-Blizzard-Marker-Token`` request header, read directly off
``request.headers`` rather than declared as a FastAPI ``Header(...)`` parameter on the
route function — declaring it there would add it to the OpenAPI spec (and the
generated web client), churning both for a credential no human caller ever supplies.
For the same reason ``chunk_id``/``node_id``/``epoch`` are read off
``request.path_params``/``request.query_params`` here rather than taking them as this
dependency's own declared parameters: FastAPI resolves a route's declared
dependencies together with its path/query params, not after them, so this dependency
cannot assume the route's own query validation has already run — a missing or
non-integer ``epoch`` is treated as "no valid token presented" and falls through,
rather than raising.
"""

from __future__ import annotations

from fastapi import Request

from blizzard.auth_core import CHUNK_CONTROL
from blizzard.hub.api.auth_session import IMPLICIT_OPERATOR, require
from blizzard.hub.api.deps import get_services
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.config import AUTH_MODE_NONE

_MARKER_TOKEN_HEADER = "X-Blizzard-Marker-Token"

#: The pre-existing human gate this dependency falls through to whenever no live
#: marker token is presented — built once so every request shares it, mirroring
#: ``require(CHUNK_CONTROL)``'s own call-site shape elsewhere in the routers.
_require_chunk_control = require(CHUNK_CONTROL)


def require_marker_authority(request: Request) -> ResolvedIdentity:
    """Grant on a live marker token for this exact node-step, else defer to
    ``require(CHUNK_CONTROL)``.

    Under ``auth.mode = "none"`` this never reaches for ``services`` (mirroring
    ``require``'s own short-circuit — ``bzh:controller-read-only`` territory
    notwithstanding, the store-free export/unit app wires no services at all): that
    mode already grants every request unconditionally, so there is nothing a token
    check could add. Under ``oauth`` a present token is checked against
    ``services.marker_authority`` before falling through.
    """
    mode = request.app.state.config.auth.mode
    if mode == AUTH_MODE_NONE:
        return IMPLICIT_OPERATOR
    token = request.headers.get(_MARKER_TOKEN_HEADER)
    if token:
        chunk_id = request.path_params.get("chunk_id")
        node_id = request.query_params.get("node_id")
        epoch_raw = request.query_params.get("epoch")
        epoch: int | None = None
        if epoch_raw is not None:
            try:
                epoch = int(epoch_raw)
            except ValueError:
                epoch = None
        if isinstance(chunk_id, str) and node_id is not None and epoch is not None:
            services = get_services(request)
            if services.marker_authority.verify(token, chunk_id=chunk_id, node_id=node_id, epoch=epoch):
                return IMPLICIT_OPERATOR
    return _require_chunk_control(request)
