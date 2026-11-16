"""A transport that sends only reads: a credential check's client carries it, so no check can write to its forge."""

from __future__ import annotations

import re

import httpx2

#: The one write a check may send: an App installation's token mint, under the forge's API base, never a repository's.
_MINT = re.compile(r"(?!.*/repos/).*/app/installations/\d+/access_tokens")


class ForgeWriteRefused(RuntimeError):
    """A read-only client was asked to send a write — a defect in the caller, never a forge answer."""


class ReadOnlyTransport(httpx2.BaseTransport):
    """Sends each ``GET`` through ``inner``, and the ``POST`` minting an App installation's token; refuses any other
    request with :class:`ForgeWriteRefused` before it is sent."""

    def __init__(self, inner: httpx2.BaseTransport) -> None:
        self._inner = inner

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        if request.method != "GET" and not (request.method == "POST" and _MINT.fullmatch(request.url.path)):
            raise ForgeWriteRefused(f"a read-only client refused to send {request.method} {request.url.path}")
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()
