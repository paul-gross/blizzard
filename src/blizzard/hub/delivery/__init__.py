"""The delivery domain — the generic hub command node executor (#65/#67).

Delivery is authored as graph CONTENT, not an engine special case: the hub executes a
node's declared ``run:`` command list behind two owned mechanism seams,
:mod:`.command_runner` and :mod:`.workdir` (``bzh:pluggable-seams``).
"""

from __future__ import annotations
