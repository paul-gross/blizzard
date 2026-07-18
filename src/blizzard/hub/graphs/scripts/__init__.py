"""Packaged `run:` scripts for the hub's shipped graphs (#67).

These are ordinary Python modules invoked by a hub command node's ``run:`` step as
``python3 -m blizzard.hub.graphs.scripts.<name>`` — a stable, installation-path-
independent invocation (the module resolves through the installed package, never a
relative path against the per-chunk hub workdir, which is a disposable cache with no
knowledge of where blizzard itself is installed). Each script is DATA the graph
declares runs, not engine code the executor calls directly (``bzh:deterministic-shell``
— the run-list is declared, never generated): the executor knows nothing about any
script here, it only ever runs the literal ``command:`` string a `run:` step names.

Every script here talks to the forge exclusively through the env a hub command node's
executor injects (``BZ_FORGE_URL``/``BZ_FORGE_TOKEN``/``BZ_FORGE_OWNER``) plus stdlib
HTTP (``urllib``) — no third-party dependency, so a script keeps working even if the
node's ``run:`` step is one day extracted into a truly separate, hub-external process.
"""

from __future__ import annotations
