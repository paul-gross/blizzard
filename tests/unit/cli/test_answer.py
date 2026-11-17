from __future__ import annotations

import httpx2
import pytest
from click import ClickException
from pydantic import BaseModel, TypeAdapter

from blizzard.cli.answer import decode_answer


class Answer(BaseModel):
    count: int


def test_preserves_raw_json_alongside_validated_model() -> None:
    response = httpx2.Response(200, json={"count": "2", "extra": "kept"})
    raw, answer = decode_answer(
        response,
        TypeAdapter(Answer),
        verb="status",
        peer="the runner",
        where="socket",
        remedy="restart it on this build",
    )
    assert raw == {"count": "2", "extra": "kept"}
    assert answer.count == 2


@pytest.mark.parametrize(
    ("body", "summary"),
    [(b'{"secret":', "invalid JSON"), (b'{"count": "secret\\nvalue"}', "1 validation error")],
)
def test_bad_answer_never_exposes_payload(body: bytes, summary: str) -> None:
    with pytest.raises(ClickException) as caught:
        decode_answer(
            httpx2.Response(200, content=body),
            TypeAdapter(Answer),
            verb="status",
            peer="the runner",
            where="socket",
            remedy="restart it on this build",
        )
    assert str(caught.value) == (
        f"status: unreadable answer from the runner at socket — restart it on this build ({summary})"
    )
    assert caught.value.__suppress_context__


def test_a_peer_with_no_remedy_names_none() -> None:
    with pytest.raises(ClickException) as caught:
        decode_answer(httpx2.Response(200, content=b"{"), TypeAdapter(Answer), verb="check", peer="the hub", where="w")
    assert str(caught.value) == "check: unreadable answer from the hub at w (invalid JSON)"


def test_the_address_drops_credentials_path_and_query() -> None:
    where = "http://user:secret@host:8431/path?token=secret#frag"
    with pytest.raises(ClickException) as caught:
        decode_answer(
            httpx2.Response(200, content=b"{"), TypeAdapter(Answer), verb="check", peer="the hub", where=where
        )
    assert str(caught.value) == "check: unreadable answer from the hub at http://host:8431 (invalid JSON)"
