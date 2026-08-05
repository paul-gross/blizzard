"""The runner's local API — the resource surface worker hooks and the CLI speak.

Controllers here read only; all mutation flows through the domain layer
(``bzh:controller-read-only``). In production this is HTTP over a unix domain socket.
The lease/bind/release/reap writes are in-process daemon writes, never routes."""

from __future__ import annotations
