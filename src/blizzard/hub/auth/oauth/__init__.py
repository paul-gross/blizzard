"""The OAuth provider seam — a Protocol (:mod:`.provider`) with two conformers
(:mod:`.internal.oidc_provider`, :mod:`.internal.github_provider`), issue #92.

A new **external system behind a new seam** (``bzh:pluggable-seams``): the provider
Protocol owns the whole authorize/exchange dance so ``hub/api/auth_login.py`` stays a
deterministic shell over it (``bzh:deterministic-shell``) — all ``httpx`` and provider
wire-shape knowledge is confined to ``internal/`` (``bzh:dependency-inversion``). The
registry (:mod:`.registry`) is built at the composition root from
``[[auth.oauth.provider]]`` config, keyed by provider ``name``.
"""

from __future__ import annotations
