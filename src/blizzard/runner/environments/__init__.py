"""The environments domain — the workspace-provider seam.

The runner acquires clean environments by opaque id before it claims a chunk.
This package owns the provider seam (:mod:`.provider`) — allocation-
stateless, clean-by-contract — and its reference bindings under ``internal/``
(``bzh:pluggable-seams``): winter worktrees, plain worktrees, or a BYO executable.
"""

from __future__ import annotations
