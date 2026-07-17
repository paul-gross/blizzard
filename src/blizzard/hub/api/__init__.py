"""The hub's HTTP edge — the route table every client speaks.

Controllers here read only; all mutation flows through the domain layer
(``bzh:controller-read-only``). The routers land under ``/api``; the web app is
mounted separately at ``/`` (``blizzard.foundation.web``).
"""

from __future__ import annotations
