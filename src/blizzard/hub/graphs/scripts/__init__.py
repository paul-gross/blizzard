"""Packaged `run:` scripts for the hub's shipped graphs (#67).

Ordinary Python modules invoked as ``python3 -m blizzard.hub.graphs.scripts.<name>``, so
they resolve through the installed package, never the disposable per-chunk workdir. Each
is DATA a graph declares (``bzh:deterministic-shell``), and uses stdlib HTTP only.
"""

from __future__ import annotations
