"""The shared SSE core both daemons bind: the kind-agnostic event broker (``src/blizzard/foundation/events/broker.py``),
the stream-response machinery (``src/blizzard/foundation/events/stream.py``), and the shutdown wrapper
(``src/blizzard/foundation/events/server.py``). A daemon's own event vocabulary, payload
models, publish wrappers, and reserved open-of-stream comment stay per-daemon; nothing
here names an event's kind or a daemon's own framing text."""

from __future__ import annotations
