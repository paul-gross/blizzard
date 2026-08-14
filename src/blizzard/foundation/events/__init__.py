"""The shared SSE core both daemons bind (D1, blizzard#317).

The kind-agnostic event broker (``broker.py``), the stream-response machinery — cursor
resolution, replay-then-live handoff, keepalive, disconnect/shutdown (``stream.py``) —
and the early-shutdown server wrapper (``server.py``). A daemon's own event vocabulary,
payload models, publish wrappers, and reserved open-of-stream comment stay per-daemon;
nothing here names an event's kind or a daemon's own framing text.
"""

from __future__ import annotations
