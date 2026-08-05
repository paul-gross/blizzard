"""The environments domain — the workspace-provider seam.

Owns the provider seam (:mod:`.provider`) — allocation-stateless, clean-by-contract — and
its reference bindings under ``internal/`` (``bzh:pluggable-seams``).
"""

from __future__ import annotations
