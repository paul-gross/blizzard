"""The client's message listing follows the service's pages to the last, never sends an order beside a cursor, and
reads one page as the service served it."""

from __future__ import annotations

from typing import Any

from blizzard.runner.harness.opencode_v2.client import MESSAGE_PAGE_LIMIT, OpenCodeServiceClient
from tests.unit.runner.harness.opencode_v2.service_fake import REGISTRATION, SessionService, SessionTransport


def _user(n: int) -> dict[str, Any]:
    return {"type": "user", "id": f"msg_{n:04d}", "time": {"created": n}, "text": str(n)}


def _client(count: int) -> tuple[OpenCodeServiceClient, SessionService]:
    service = SessionService()
    service.add_session("ses_1")
    service.messages["ses_1"] = [_user(n) for n in range(count)]
    return OpenCodeServiceClient(REGISTRATION, SessionTransport(service)), service


def test_the_listing_follows_every_page_and_never_sends_an_order_with_a_cursor() -> None:
    client, service = _client(2 * MESSAGE_PAGE_LIMIT + 7)
    listed = client.messages("ses_1")
    assert [m["id"] for m in listed] == [m["id"] for m in service.messages["ses_1"]]
    queries = [query for _, query in service.listings]
    assert len(queries) == 3 and all(("cursor" in query) != ("order" in query) for query in queries)


def test_a_newest_first_listing_carries_its_order_through_the_cursor() -> None:
    client, service = _client(MESSAGE_PAGE_LIMIT + 1)
    listed = client.messages("ses_1", order="desc")
    assert [m["id"] for m in listed] == [m["id"] for m in reversed(service.messages["ses_1"])]


def test_one_page_carries_its_items_its_cursor_and_the_envelope_as_served() -> None:
    client, _ = _client(3)
    page = client.message_page("ses_1", limit=2)
    assert [m["id"] for m in page.items] == ["msg_0000", "msg_0001"] and page.next is not None
    assert set(page.raw) == {"data", "cursor"}
    rest = client.message_page("ses_1", cursor=page.next, limit=2)
    assert [m["id"] for m in rest.items] == ["msg_0002"]
    assert client.message_page("ses_1", cursor=rest.next).next is None
