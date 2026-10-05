"""The frontend mount seam — StaticFiles + SPA fallback.

A deep client-side route resolves to ``index.html`` instead of 404."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, TypeAdapter
from pydantic.json_schema import models_json_schema
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from blizzard.foundation.assets import EmbeddedFrontend
from blizzard.foundation.roles import collaborator

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


@collaborator
@dataclass(frozen=True)
class Frontend:
    """One app's frontend mount — its bundle directory and its placeholder name."""

    directory: Path
    app_name: str

    @classmethod
    def embedded(cls, app: str, *, app_name: str) -> Frontend:
        """The wheel-embedded bundle for ``app`` (``hub`` / ``runner``)."""
        return cls(EmbeddedFrontend(app).directory, app_name)

    def mount(self, app: FastAPI) -> None:
        """Serves a placeholder when ``index.html`` is absent, so the mount point is
        always live. Call **after** the API routers."""
        index = self.directory / "index.html"
        if index.exists():
            app.mount("/", SpaStaticFiles(directory=str(self.directory), html=True), name="web")
            return

        placeholder = _PLACEHOLDER_HTML.format(app_name=self.app_name)

        @app.get("/", include_in_schema=False)
        def _web_root() -> HTMLResponse:
            return HTMLResponse(placeholder)


_COMPONENT_REF = "#/components/schemas/{model}"


class SchemaComponentCollision(ValueError):
    """An extra component named the same as one the routes already reach, with a different schema."""


def install_schema_components(app: FastAPI, models: Sequence[type[BaseModel]], enums: Mapping[str, object]) -> None:
    """Wrap ``app.openapi`` so the spec also carries ``models`` (serialization mode) and the
    vocabularies in ``enums`` (keyed by component name) under ``components.schemas`` — the
    served and the exported spec alike. A name the routes already reach must carry the
    identical schema; a differing one raises :class:`SchemaComponentCollision`."""
    route_spec: Callable[[], dict[str, Any]] = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        spec = route_spec()
        schemas: dict[str, Any] = spec.setdefault("components", {}).setdefault("schemas", {})
        _, top = models_json_schema([(m, "serialization") for m in models], ref_template=_COMPONENT_REF)
        extra: dict[str, Any] = dict(top.get("$defs", {}))
        for name, vocabulary in enums.items():
            extra[name] = TypeAdapter(vocabulary).json_schema(mode="serialization", ref_template=_COMPONENT_REF)
        for name, schema in extra.items():
            if name in schemas and schemas[name] != schema:
                raise SchemaComponentCollision(f"component {name!r} collides with a different route schema")
            schemas[name] = schema
        app.openapi_schema = spec
        return spec

    app.openapi = openapi  # type: ignore[method-assign]
