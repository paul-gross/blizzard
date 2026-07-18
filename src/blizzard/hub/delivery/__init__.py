"""The delivery domain — the generic hub command node executor (#65/#67).

Delivery is authored as graph CONTENT, not an engine special case: the hub itself
executes a node's declared ``run:`` command list (:mod:`.hub_node`) behind two owned
mechanism seams, :mod:`.command_runner` (subprocess) and :mod:`.workdir` (the
per-chunk temp folder), with their reference bindings under ``internal/``
(``bzh:pluggable-seams``). A ``run:`` script talks to the forge itself, through plain
injected env (``BZ_FORGE_URL``/``BZ_FORGE_TOKEN``/``BZ_FORGE_OWNER``) and stdlib
``urllib`` — no forge seam lives in this package; the policy is the script, not
engine code (``bzh:deterministic-shell``).
"""

from __future__ import annotations
