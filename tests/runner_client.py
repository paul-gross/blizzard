"""Clients that address one runner on a host app: its routes mount under ``/runners/<runner_id>``, so a client whose
base URL carries that prefix keeps every ``/api/...`` path a test calls unchanged."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute, _IncludedRouter
from fastapi.testclient import TestClient

from blizzard.runner.api.wiring import RunnerRegistry
from blizzard.runner.route_paths import runner_prefix


def sole_runner_id(app: FastAPI) -> str:
    """The id of the one runner ``app`` serves."""
    registry: RunnerRegistry = app.state.runners
    (runner,) = registry.served
    return runner.runner_id


def runner_base_url(app: FastAPI, origin: str = "http://testserver") -> str:
    """``origin`` under the prefix of the one runner ``app`` serves."""
    return origin + runner_prefix(sole_runner_id(app))


class UntimedTestClient(TestClient):
    """A :class:`TestClient` for the client a production seam hands a per-request ``timeout=`` to: Starlette's
    in-process transport never honored one and warns when it is passed, so the budget is dropped here and the seam's
    code, which really sets it, stays untouched."""

    def request(self, method: str, url: Any, **kwargs: Any) -> Any:
        kwargs.pop("timeout", None)
        return super().request(method, url, **kwargs)


def runner_client(app: FastAPI, **kwargs: Any) -> TestClient:
    """A :class:`TestClient` addressing the one runner ``app`` serves; ``kwargs`` are the client's own."""
    return TestClient(app, base_url=runner_base_url(app, kwargs.pop("base_url", "http://testserver")), **kwargs)


def runner_seams(app: FastAPI) -> Any:
    """The seams of the one runner ``app`` serves — where a test reads or swaps a runner-scoped collaborator."""
    registry: RunnerRegistry = app.state.runners
    (runner,) = registry.served
    return runner.seams


def live_api_routes(app: FastAPI) -> list[tuple[str, APIRoute]]:
    """Every mounted API route with the full path it answers at, its include prefix applied; the web root, the SPA
    shell, is left out."""

    def collect(routes: object, prefix: str) -> list[tuple[str, APIRoute]]:
        out: list[tuple[str, APIRoute]] = []
        for route in routes:  # type: ignore[attr-defined]
            if isinstance(route, APIRoute):
                if prefix + route.path != "/":
                    out.append((prefix + route.path, route))
            elif isinstance(route, _IncludedRouter):
                out.extend(collect(route.original_router.routes, prefix + route.include_context.prefix))
        return out

    return collect(app.routes, "")
