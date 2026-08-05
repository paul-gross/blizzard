"""The marker-write route's own authorization dependency (issue #230, phase 2).

A second way past that route's gate, layered in front of the human
``require(CHUNK_CONTROL)`` rather than replacing it: a live marker token for the exact
``(chunk_id, node_id, epoch)`` triple grants without a session, else it falls through.
"""

from __future__ import annotations

from fastapi import Request

from blizzard.auth_core import CHUNK_CONTROL
from blizzard.hub.api.auth_session import IMPLICIT_OPERATOR, require
from blizzard.hub.api.deps import get_services
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.config import AUTH_MODE_NONE

_MARKER_TOKEN_HEADER = "X-Blizzard-Marker-Token"

#: The human gate this dependency falls through to, built once and shared.
_require_chunk_control = require(CHUNK_CONTROL)


def require_marker_authority(request: Request) -> ResolvedIdentity:
    """Grant on a live marker token for this exact node-step, else defer to the human gate.

    Path and query params are read off the request because FastAPI resolves a route's
    dependencies alongside its own param validation: a bad ``epoch`` falls through.
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
