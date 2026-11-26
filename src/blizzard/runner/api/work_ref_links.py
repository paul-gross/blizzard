"""Translate the hub's own relative board links at the runner proxy boundary."""

from __future__ import annotations

from urllib.parse import urlsplit


def hub_work_ref_url(source: str, url: str | None, hub_url: str) -> str | None:
    """Keep forge and absolute links intact; anchor only hub-authored board paths at the hub."""
    if source != "hub" or url is None or not url.startswith("/board/chunk/"):
        return url
    origin = urlsplit(hub_url)
    if origin.scheme not in {"http", "https"} or not origin.netloc or origin.username is not None:
        return None
    return f"{origin.scheme}://{origin.netloc}{url}"
