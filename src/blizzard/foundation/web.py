"""The frontend mount seam — StaticFiles + SPA fallback (D-096).

FastAPI serves the client-rendered Angular app from the same process and origin
as ``/api`` and the SSE streams: the build output is mounted with an SPA fallback
so a deep client-side route (``/board/123``) resolves to ``index.html`` rather
than 404. Registered **after** the API routers, so ``/api/*`` always wins.

Until the real assets exist, each daemon ships a placeholder ``index.html`` (see
``blizzard/static/README.md``); the mount serves it unchanged, and CI overwrites
it with the compiled app before the wheel is built.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

_PLACEHOLDER_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{app_name}</title></head>
<body style="font-family: ui-monospace, monospace; background:#0b0e14; color:#cdd6f4;
             display:flex; min-height:100vh; align-items:center; justify-content:center;">
  <main style="text-align:center;">
    <h1>{app_name}</h1>
    <p>Frontend assets are not built yet — this is the embedded placeholder.</p>
    <p>The compiled Angular app is filled in by CI before the wheel is built.</p>
  </main>
</body></html>
"""


class SpaStaticFiles(StaticFiles):
    """StaticFiles that falls back to ``index.html`` for unmatched paths (SPA routing)."""

    async def get_response(self, path: str, scope: Any) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


def mount_web_app(app: FastAPI, static_dir: Path, *, app_name: str) -> None:
    """Mount the embedded frontend for ``app_name`` at ``/`` with an SPA fallback.

    When ``static_dir/index.html`` exists (the committed placeholder or a real
    build) it is served via :class:`SpaStaticFiles`; otherwise a placeholder page
    is served so the mount point is always live. Call **after** the API routers.
    """
    index = static_dir / "index.html"
    if index.exists():
        app.mount("/", SpaStaticFiles(directory=str(static_dir), html=True), name="web")
        return

    placeholder = _PLACEHOLDER_HTML.format(app_name=app_name)

    @app.get("/", include_in_schema=False)
    def _web_root() -> HTMLResponse:
        return HTMLResponse(placeholder)
