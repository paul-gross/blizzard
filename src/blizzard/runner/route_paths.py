"""Where a host serves each runner, and the one reading of "is this an API path".

The host keeps its own routes at the root and mounts each runner's under ``/runners/<runner_id>``. An API path is
``/api/…`` at the root, or ``/api/…`` or an OTLP receiver path under a runner's prefix; every other path is the
panel's, which the web gate guards and the SPA fallback answers."""

from __future__ import annotations

RUNNERS_SEGMENT = "/runners"
#: The prefix every runner-owned router mounts under, with ``runner_id`` its path parameter.
RUNNER_ROUTE_PREFIX = RUNNERS_SEGMENT + "/{runner_id}"
OTLP_PATHS = frozenset({"/v1/traces", "/v1/metrics", "/v1/logs"})


def runner_prefix(runner_id: str) -> str:
    """The path one runner's routes are served under."""
    return f"{RUNNERS_SEGMENT}/{runner_id}"


def within_runner(path: str) -> str | None:
    """``path`` relative to the runner prefix it carries, or ``None`` when it carries none."""
    if not path.startswith(RUNNERS_SEGMENT + "/"):
        return None
    runner_id, slash, rest = path[len(RUNNERS_SEGMENT) + 1 :].partition("/")
    return "/" + rest if runner_id and slash else None


def _is_root_api(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def is_api_path(path: str) -> bool:
    """Whether ``path`` addresses the API rather than the panel."""
    if _is_root_api(path):
        return True
    relative = within_runner(path)
    return relative is not None and (_is_root_api(relative) or relative.rstrip("/") in OTLP_PATHS)
