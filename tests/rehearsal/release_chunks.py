"""Read candidate chunk pages through the installed release's CLI pagination path."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx2 as httpx
else:
    try:
        import httpx2 as httpx
    except ImportError:
        import httpx

from blizzard.hub.cli.context import CliContext


class PageReader(CliContext):
    """Use small real pages while keeping the release's continuation logic."""

    transport: httpx.Client
    requests: list[dict[str, str]]
    pages: list[dict[str, Any]]

    def get(
        self,
        path: str,
        operation: str,
        *,
        params: dict[str, str] | None = None,
        on_status: dict[int, str] | None = None,
    ) -> Any:
        query = dict(params or {})
        query["limit"] = "1"
        self.requests.append(query)
        response = self.transport.get(path, params=query)
        response.raise_for_status()
        self.pages.append(response.json())
        return response


def main() -> None:
    url = sys.argv[1]
    with httpx.Client(base_url=url, timeout=30) as transport:
        for board_window in (False, True):
            reader = object.__new__(PageReader)
            object.__setattr__(reader, "transport", transport)
            object.__setattr__(reader, "requests", [])
            object.__setattr__(reader, "pages", [])
            params = {"board_window": "true"} if board_window else {}
            rows = reader.get_all("/api/chunks", "GET /chunks", key="chunks", params=params)
            pages = reader.pages
            assert len(pages) >= 2, "the probe needs at least two chunk pages"
            for index, page in enumerate(pages):
                policy = page["board_done_policy"]
                if board_window:
                    assert isinstance(policy, dict), policy
                    assert all(key in policy for key in ("window_seconds", "floor", "done_since")), policy
                else:
                    assert policy is None, policy
                if index:
                    assert reader.requests[index]["cursor"] == pages[index - 1]["next_cursor"]
                if index < len(pages) - 1:
                    assert page["next_cursor"] is not None
            assert pages[-1]["next_cursor"] is None
            assert [row["chunk_id"] for row in rows] == [row["chunk_id"] for page in pages for row in page["chunks"]]


if __name__ == "__main__":
    main()
