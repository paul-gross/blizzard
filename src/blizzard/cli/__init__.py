"""The ``blizzard`` CLI — one binary, verbs namespaced by target (D-061).

``blizzard runner <cmd>`` hits the runner's local API, ``blizzard hub <cmd>`` hits
the hub's HTTP API, and each noun's ``host`` verb *becomes* that daemon. A pure
client: the client verbs never open a store; the ``host`` personalities are the
same binary's daemons. This package is top-level glue — ``echo`` for user output
is fine here; diagnostics go through structlog in the runtimes and apps.
"""

from __future__ import annotations
